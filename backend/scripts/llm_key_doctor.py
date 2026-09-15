"""LLM Key 体检 —— 先分清「账户有没有资源」与「这把 Key 能不能花到那笔资源」。

为什么有这支脚本
----------------
2026-09-16 H7a 首跑：24 次调用全部 HTTP 402 ``insufficient balance (1008)``，
当时记的结论是「账户余额不足，充值后重跑」。但账户**本来就有** Token Plan Plus 订阅
（``/v1/token_plan/remains`` 显示周窗口剩余 99%）—— 也就是说结论方向错了，
照着做会去充一笔不需要充的钱。

按 MiniMax 官方文档（Token Plan 概要 / FAQ / 常见问题），平台有**两套互不通用的凭证**：

===================== ============================== ==========================
凭证                   花的是                          拿到的地方
===================== ============================== ==========================
订阅 Key               Token Plan 套餐额度 + 已购积分   账户管理 / Token Plan
按量计费 API Key        开放平台**账户余额**（wallet）    接口密钥页
===================== ============================== ==========================

官方原话：「订阅 Key 与普通按量计费 API Key **相互独立，不能混用**。」
所以「账户有钱」与「这把 Key 能花到那笔钱」是两件事。402/1008 既可能是钱包真空，
也可能是**Key 类型配错** —— 拿着按量计费的 Key 去花订阅额度，钱在套餐里、却一间空钱包扣款。

检查项（4 步，逐步收窄）
------------------------
1. **形态判读** 前缀/长度 ⇒ 这把 Key 大概率属于哪一类（形态旁证，非平台确证）
2. ``GET  /models``              ⇒ Key 是否被该站点认可（跨站 Key 会返 2049）
3. ``GET  /token_plan/remains``  ⇒ 该账号套餐的窗口与剩余百分比
4. ``POST /chat/completions``    ⇒ 端到端最小调用；把错误码翻译成「该做什么」

退出码：``0`` 能调通 / ``1`` 有阻塞 / ``2`` 无法判定（网络等）。
**只读**：不做任何写操作，不打印 Key 全文，不落盘密钥。

用法（cwd=backend）::

    .venv/Scripts/python.exe scripts/llm_key_doctor.py
    .venv/Scripts/python.exe scripts/llm_key_doctor.py --key sk-cp-xxxx --no-proxy
    .venv/Scripts/python.exe scripts/llm_key_doctor.py --json ../artifacts/llm-key-doctor.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

# 以 `python scripts/llm_key_doctor.py`（cwd=backend）执行时，`app` 不在 sys.path 上，
# 需显式补上 backend/ —— 这是本文件唯一需要的 sys.path 操作。
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core.config import get_settings  # noqa: E402

#: MiniMax 错误码 → (含义, 该做什么)。
#: 出处：MiniMax 官方错误码参考；1008 与 2049 于 2026-09-16 本机实测复现。
MINIMAX_CODES: dict[int, tuple[str, str]] = {
    1002: ("限流", "降低并发或稍后重试"),
    1004: ("未授权 / token 与分组不匹配", "检查 Key 所属团队、站点区域与生效状态"),
    1008: ("余额或可用资源不足", "见下方「修复指引」—— 关键是先分清哪笔资源"),
    2049: (
        "API Key 无效",
        "核对 Key 与站点：国内 api.minimaxi.com / 海外 api.minimax.io，两站 Key 不通用",
    ),
    2056: ("套餐额度已用尽", "等 5 小时窗口重置、用已购积分、升级档位，或切按量计费"),
}

#: Key 前缀 → (类别, 花的是哪笔资源)。
#: ⚠️ 这是**形态旁证**：官方文档只规定「两套 Key 互不通用」，并未公布前缀对照表；
#: 前缀命名来自社区实践。最终以控制台页面（Key 是从哪一页拿的）为准。
KEY_PREFIX_HINTS: tuple[tuple[str, str, str], ...] = (
    ("sk-cp-", "subscription", "订阅 Key —— 扣 Token Plan 套餐额度 / 已购积分"),
    ("sk-api-", "pay-as-you-go", "按量计费 Key —— 扣开放平台账户余额（wallet）"),
)

#: 订阅 Key / 套餐状态页（Token Plan）。页址若变动，走菜单路径：
#: 「MiniMax 开放平台 → 账户管理 → Token Plan → 查看订阅 Key」。
_CONSOLE_SUBSCRIPTION_KEY = "https://platform.minimaxi.com/console/plan"
#: 按量计费 Key（走开放平台账户余额）
_CONSOLE_API_KEY = "https://platform.minimaxi.com/user-center/basic-information/interface-key"


def _mask(key: str) -> str:
    """脱敏展示：够长才显示首尾，否则视为占位符。"""
    if len(key) < 16:
        return f"（过短/占位，{len(key)} 字符）"
    return f"{key[:10]}…{key[-4:]}（{len(key)} 字符）"


def _key_shape(key: str) -> tuple[str, str]:
    """按前缀判读 Key 类别，返回 ``(类别, 说明)``。"""
    for prefix, kind, desc in KEY_PREFIX_HINTS:
        if key.startswith(prefix):
            return kind, f"{desc}（前缀 {prefix}）"
    return "unknown", "前缀不在已知形态内 —— 请到控制台看这把 Key 是从哪一页创建的"


def _code_of(body: Any) -> int | None:
    """从 MiniMax 错误体里抠出 4 位数字错误码。"""
    if not isinstance(body, dict):
        return None
    err = body.get("error") if isinstance(body.get("error"), dict) else body
    base = body.get("base_resp") if isinstance(body.get("base_resp"), dict) else {}
    for src in (err, base):
        if not isinstance(src, dict):
            continue
        m = re.search(r"\b(\d{4})\b", str(src.get("message") or src.get("status_msg") or ""))
        if m:
            return int(m.group(1))
        sc = src.get("status_code")
        if isinstance(sc, int) and sc >= 1000:
            return sc
    return None


def _explain(status: int | None, body: Any) -> str:
    """把一次响应压成一句人话。"""
    if status is None:
        return f"网络层失败：{body}"
    if status == 200:
        # ⚠️ 有的端点用「HTTP 200 + body 里的 status_code」表达错误：
        # 实测 MiniMax 的 /token_plan/remains 拿**无效 Key** 也返 200，
        # body 是 {"base_resp":{"status_code":2049,"status_msg":"invalid api key"}}。
        # 只看 HTTP 码会把「Key 无效」报成「200 OK」—— 这是本脚本自查时才发现的。
        code = _code_of(body)
        if code:
            name, action = MINIMAX_CODES.get(code, ("未在对照表内", "查供应商文档"))
            return f"HTTP 200（body 内报错）/{code} {name} ⇒ {action}"
        return "200 OK"
    code = _code_of(body)
    if code is not None and code in MINIMAX_CODES:
        name, action = MINIMAX_CODES[code]
        return f"HTTP {status} / {code} {name} ⇒ {action}"
    err = body.get("error") if isinstance(body, dict) else None
    msg = ""
    if isinstance(err, dict):
        msg = str(err.get("message") or err.get("type") or "")
    elif isinstance(body, dict):
        msg = str(body.get("message") or "")
    return f"HTTP {status} ⇒ {msg[:120] or str(body)[:120]}"


def _call(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> tuple[int | None, Any]:
    """发一次请求，永不抛；返回 ``(状态码 or None, 解析后的响应体 or 文本)``。"""
    try:
        resp = client.request(method, url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text[:400]


def _verdict(shape: str, chat_status: int | None, chat_body: Any) -> tuple[int, str, list[str]]:
    """给出总判与修复指引，返回 ``(退出码, 一行结论, 指引列表)``。"""
    if chat_status == 200 and not _code_of(chat_body):
        return 0, "✅ 端到端可调用 —— 这把 Key 能花到资源", []

    code = _code_of(chat_body)
    if chat_status is None:
        return (
            2,
            "⚠️ 无法判定：网络层失败（可加 --no-proxy 重试）",
            [
                "本机若设了 http_proxy，代理可能拦截对 API 站点的请求；用 --no-proxy 再试。",
            ],
        )

    steps: list[str] = []
    if code == 1008:
        if shape == "pay-as-you-go":
            conclusion = (
                "❌ 有阻塞，且**不是「没买订阅」**：这把 Key 是按量计费形态，"
                "扣的是开放平台账户余额（wallet）；余额为 0 就报 1008，"
                "与 Token Plan 套餐里剩多少额度无关。"
            )
            steps = [
                f"① 去订阅管理页复制**订阅 Key**：{_CONSOLE_SUBSCRIPTION_KEY}"
                "（打不开就走菜单：账户管理 → Token Plan → 查看订阅 Key）",
                "② 用它替换 .env.local 的 LLM_API_KEY（订阅 Key 与按量计费 Key 不能混用）",
                f"③ 顺带核对这把按量计费 Key 的来源：{_CONSOLE_API_KEY}",
                "④ 改完重跑本脚本：应当看到「端到端可调用」",
            ]
        elif shape == "subscription":
            conclusion = (
                "❌ 有阻塞：这已经是订阅 Key，但套餐侧没有可用的付费资源 "
                "（席位未分配 / 已用尽 / 订阅未生效）。"
            )
            steps = [
                "① 打开订阅管理页，确认该席位已分配给当前 Key 所属的团队与用户",
                "② 若显示额度用尽 ⇒ 等 5 小时窗口重置、用已购积分、升级档位，或切按量计费",
            ]
        else:
            conclusion = "❌ 有阻塞：报 1008（余额/资源不足），但无法从形态判断 Key 类别。"
            steps = [
                f"① 到订阅管理页确认订阅 Key：{_CONSOLE_SUBSCRIPTION_KEY}",
                f"② 到接口密钥页确认按量计费 Key：{_CONSOLE_API_KEY}",
                "③ 用订阅 Key 重跑本脚本以定位是哪一侧缺资源",
            ]
    elif code == 2049:
        conclusion = "❌ 有阻塞：Key 与站点区域不匹配或被停用。"
        steps = [
            "① 国内站 Key 配 api.minimaxi.com / api.minimax.chat；海外站 Key 配 api.minimax.io",
            "② 两站账号与 Key 互不通用，确认这把 Key 是在哪个站点创建的",
        ]
    elif code == 2056:
        conclusion = "❌ 有阻塞：套餐额度窗口已用尽（等重置或用积分）。"
        steps = ["① 等 5 小时窗口重置；② 或改用已购积分 / 升级档位"]
    elif chat_status in (401, 403):
        conclusion = "❌ 有阻塞：鉴权失败。"
        steps = ["① 核对 Key 是否被撤销；② 确认 LLM_BASE_URL 与 Key 站点一致"]
    elif chat_status == 429:
        conclusion = "⚠️ 被限流，不是配置问题。"
        steps = ["① 降低请求频率后重试"]
    else:
        conclusion = f"❌ 有阻塞：{_explain(chat_status, chat_body)}"
        steps = ["① 对照上面这行的错误码含义处理"]

    steps.append("修好后：cd backend && .venv/Scripts/python.exe scripts/h7a_model_quality.py")
    return 1, conclusion, steps


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LLM Key 体检：分清「账户有资源」与「这把 Key 能花到那笔资源」",
    )
    p.add_argument("--key", default=None, help="临时试一把 Key（不落盘，优先于 .env.local）")
    p.add_argument("--base-url", default=None, help="覆盖 LLM_BASE_URL，便于试不同站点")
    p.add_argument("--model", default=None, help="覆盖 LLM_MODEL")
    p.add_argument(
        "--no-proxy", action="store_true", help="忽略 HTTP(S)_PROXY 环境变量（本机有代理时用）"
    )
    p.add_argument("--json", dest="json_path", default=None, help="把结果写到该路径")
    p.add_argument("--timeout", type=float, default=30.0, help="单次请求超时（秒）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()

    key = args.key or settings.LLM_API_KEY
    base_url = (args.base_url or settings.LLM_BASE_URL).rstrip("/")
    model = args.model or settings.LLM_MODEL
    trust_env = not args.no_proxy

    print("=" * 74)
    print("LLM Key 体检")
    print("=" * 74)
    print(f"provider : {settings.LLM_PROVIDER}")
    print(f"base_url : {base_url}")
    print(f"model    : {model}")
    print(f"LLM_MOCK : {settings.LLM_MOCK}")
    print(f"key      : {_mask(key)}")
    proxies = {
        k: v
        for k, v in os.environ.items()
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy")
    }
    print(f"代理     : {'忽略（--no-proxy）' if not trust_env else (proxies or '未设置')}")

    result: dict[str, Any] = {
        "base_url": base_url,
        "model": model,
        "mock": settings.LLM_MOCK,
        "key_masked": _mask(key),
    }

    if not key:
        print("\n❌ LLM_API_KEY 为空 —— 真实模式下不会发起任何调用（会自动降级到 mock 模板）。")
        print(f"① 到 Token Plan 页取订阅 Key：{_CONSOLE_SUBSCRIPTION_KEY}")
        print("② 写入 backend/.env.local 的 LLM_API_KEY=…（该文件不入库）")
        return 1

    shape, shape_desc = _key_shape(key)
    print(f"\n① Key 形态：{shape} —— {shape_desc}")
    result["key_shape"] = shape

    headers = {"Authorization": f"Bearer {key}"}
    with httpx.Client(timeout=args.timeout, trust_env=trust_env) as client:
        # ---- ② 模型表：Key 是否被该站点认可 ----
        st, body = _call(client, "GET", f"{base_url}/models", headers)
        n_models = len(body.get("data", [])) if isinstance(body, dict) else 0
        ids = [m.get("id") for m in body.get("data", [])][:6] if isinstance(body, dict) else []
        print(f"② GET  /models             : {_explain(st, body)}")
        if st == 200:
            print(f"   该站点可用模型：{n_models} 个，前几个 {ids}")
        result["models"] = {"status": st, "count": n_models, "ids": ids}

        # ---- ③ Token Plan 剩余额度 ----
        st2, body2 = _call(client, "GET", f"{base_url}/token_plan/remains", headers)
        print(f"③ GET  /token_plan/remains : {_explain(st2, body2)}")
        remains = None
        # 200 也可能是「body 内报错」（见 _explain）：那种情况解析不出套餐，别硬解析
        if st2 == 200 and isinstance(body2, dict) and not _code_of(body2):
            remains = [
                {
                    "model_name": m.get("model_name"),
                    "interval_total": m.get("current_interval_total_count"),
                    "interval_used": m.get("current_interval_usage_count"),
                    "interval_remaining_percent": m.get("current_interval_remaining_percent"),
                    "weekly_remaining_percent": m.get("current_weekly_remaining_percent"),
                }
                for m in body2.get("model_remains", [])
            ]
            for m in remains:
                print(
                    f"   {m['model_name']:8s} 5h 总配额={m['interval_total']} "
                    f"已用={m['interval_used']} 剩余={m['interval_remaining_percent']}%  "
                    f"周剩余={m['weekly_remaining_percent']}%"
                )
            print("   注：token_plan/remains 反映的是**账号/团队**的套餐状态。")
        result["token_plan_remains"] = remains

        # ---- ④ 端到端最小调用 ----
        st3, body3 = _call(
            client,
            "POST",
            f"{base_url}/chat/completions",
            headers,
            {
                "model": model,
                "messages": [{"role": "user", "content": "只回复两个字：收到"}],
                "max_tokens": 16,
                "stream": False,
            },
        )
        print(f"④ POST /chat/completions   : {_explain(st3, body3)}")
        if st3 == 200 and isinstance(body3, dict):
            try:
                text = body3["choices"][0]["message"]["content"]
                print(f"   模型回复：{text!r}  usage={body3.get('usage')}")
            except (KeyError, IndexError, TypeError):
                print("   200 但响应体形状异常")
        result["chat"] = {"status": st3, "code": _code_of(body3)}

    rc, conclusion, steps = _verdict(shape, result["chat"]["status"], body3)
    result["conclusion"] = conclusion
    result["exit_code"] = rc
    print("\n" + "=" * 74)
    print(conclusion)
    if steps:
        print("-" * 74)
        for s in steps:
            print(s)
    print("=" * 74)

    if args.json_path:
        p = Path(args.json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"（结果已写入 {p}）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
