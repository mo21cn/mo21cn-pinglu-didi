"""云端演示就绪自检（**只读**）—— 把「代码层」与「种子层」分开证。

用法（在仓库根或任意目录均可）：

    python backend/scripts/check_demo_readiness.py
    python backend/scripts/check_demo_readiness.py --base-url https://<你的服务域名> --json

为什么要有这个脚本
------------------
DEMO-1 工作单 E-0.11 原来把「云端已通」写成一个档。但**外部 HTTP 探测只能证三件事**：
容器活着、关务路由已部署、鉴权层在线；它**证不了** `boot_seed` 有没有铺关务种子 ——
那两行（`SEED_CUSTOMS_OWNER 已显式设置` / `关务演示数据就绪`）只存在于容器日志里。
把两层混成一档，会让 HO 去重做已经完成的重部署，也会让人把「接口在线」
误当成「演示数据已就绪」。所以这里把结论拆成 `code_layer_ready` 与
恒为 False 的 `seed_layer_proven`，并明确写出各自的证据来源。

网关层错误（`server: cbrgw`）单独诊断：402 `ENV_ISOLATED` 是环境欠费被隔离、
404 `INVALID_HOST` 是默认域名已注销 / 服务未部署 —— 两者请求都没到容器，
⛔ 都不能翻译成「代码没部署」。2026-11-08 / 11-26 两次实证后补上 `gateway_diagnosis`。

⛔ 硬约束：只发 GET；不登录、不写云端、不携带任何凭据、不读环境变量里的密钥。
   §15 走查里有归类发起（POST），那属于设备侧动作，⛔ 不在本脚本范围内。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

#: 2026-09-23 云端部署执行记录里的对外域名（`wx.cloud.callContainer` 之外的 HTTPS 探测口）。
DEFAULT_BASE_URL = "https://pinglu-backend-318466-5-1494250212.sh.run.tcloudbase.com"

#: #207 的 `backend/app/modules/customs/router.py` —— 7 条路径 / 9 个接口（GET+POST 合并）。
#: 用**精确集合**而不是「包含 customs 字样的路径数」：后者在只部署一半路由时也会绿。
EXPECTED_CUSTOMS_PATHS = frozenset(
    {
        "/api/v1/customs/sources",
        "/api/v1/customs/declarations",
        "/api/v1/customs/declarations/{declaration_id}",
        "/api/v1/customs/declarations/{declaration_id}/events",
        "/api/v1/customs/classifications",
        "/api/v1/customs/classifications/{classification_id}",
        "/api/v1/customs/classifications/{classification_id}/manual",
    }
)

#: 演示当天小程序必须走 `demo` 档（云托管）；`dev` 档走本地回环，不是演示的那条路。
EXPECTED_PROFILE = "demo"
PROFILE_RE = re.compile(r"^const RUNTIME_PROFILE = '([^']+)'", re.MULTILINE)

SEED_NOTE = (
    "关务种子由 boot_seed 在容器启动时铺；外部 HTTP 看不到它。"
    "唯一证据是容器日志的『SEED_CUSTOMS_OWNER 已显式设置』与『关务演示数据就绪』两行。"
    "代码层全绿 ≠ 种子已铺，⛔ 不得据此宣布云端已通或进入 §15 demo 档走查。"
)

#: 云托管**网关层**错误码 →（含义、恢复动作）。这些响应由 `cbrgw` 直接给出，
#: 请求根本没到容器。⛔ 一律不能翻译成「代码没部署」——那会让人去重复合并/部署代码，
#: 而真正要修的是环境付费状态或服务的部署/域名绑定（2026-11-08 / 11-26 两次实证）。
GATEWAY_DIAGNOSIS: dict[str, tuple[str, str]] = {
    "ENV_ISOLATED": (
        "云托管环境被隔离（套餐到期未续费 / 欠费）",
        "HO 到 https://tcb.cloud.tencent.com/dev 续费；续费后重跑本自检",
    ),
    "INVALID_HOST": (
        "云托管默认域名已从网关注销 / 服务 pinglu-backend 未部署",
        "HO 在云托管控制台重建或重新部署服务，取到新域名后用 --base-url 复跑；"
        "若环境 ID 变更，同步 miniapp/config/env.js 的 CLOUD_ENV_ID",
    ),
}

GATEWAY_NOTE = (
    "⛔ 该错误来自 cbrgw 网关层，请求未到达容器；上面的 FAIL 不是「代码没部署」，"
    "不要据此重复合并/部署代码。"
)


def _gateway_code(body: str) -> str:
    """从网关 JSON 响应里取 `code`；非 JSON / 非字典 / 非字符串一律回空串（⛔ 不吞错也不臆测）。"""
    try:
        doc = json.loads(body)
    except (ValueError, TypeError):
        return ""
    code = doc.get("code") if isinstance(doc, dict) else None
    return code if isinstance(code, str) else ""


def _get(base_url: str, path: str, timeout: float) -> tuple[int, str]:
    """只读 GET。HTTP 错误照实回码；网络异常回 -1 并把原因写进 evidence（⛔ 不吞错）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = base_url.rstrip("/") + path
    try:
        with opener.open(url, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (OSError, ValueError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def _check(name: str, ok: bool, evidence: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "evidence": evidence}


def read_runtime_profile(repo: Path) -> tuple[str, str]:
    """读 `miniapp/config/env.js` 的 `RUNTIME_PROFILE`；读不到回空串 + 原因。"""
    path = repo / "miniapp" / "config" / "env.js"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return "", f"{type(exc).__name__}: {path}"
    hit = PROFILE_RE.search(text)
    return (hit.group(1), str(path)) if hit else ("", f"找不到 RUNTIME_PROFILE：{path}")


def probe(base_url: str, repo: Path, timeout: float = 15.0) -> dict[str, Any]:
    """跑完四项只读检查，返回可序列化结论。⛔ 不抛异常给调用方，失败以 check 形式落盘。"""
    checks: list[dict[str, Any]] = []
    bodies: list[str] = []

    code, body = _get(base_url, "/healthz", timeout)
    bodies.append(body)
    health_ok = False
    with contextlib.suppress(ValueError, AttributeError):
        health_ok = code == 200 and json.loads(body).get("status") == "ok"
    checks.append(_check("healthz", health_ok, f"HTTP {code} body={body[:160]}"))

    code, body = _get(base_url, "/openapi.json", timeout)
    bodies.append(body)
    missing: list[str] = []
    paths_note = ""
    try:
        doc = json.loads(body) if code == 200 else {}
        paths = set(doc.get("paths", {}))
        missing = sorted(EXPECTED_CUSTOMS_PATHS - paths)
        paths_note = f"HTTP {code} paths={len(paths)} missing={missing}"
    except (ValueError, AttributeError, TypeError) as exc:
        paths_note = f"HTTP {code} openapi 解析失败 {type(exc).__name__}: {exc}"
    checks.append(_check("customs_routes", code == 200 and not missing, paths_note))

    code, body = _get(base_url, "/api/v1/customs/sources", timeout)
    bodies.append(body)
    checks.append(
        _check(
            "customs_auth",
            code == 401,
            f"HTTP {code} body={body[:160]}（期望 401 未提供访问令牌，不是 404/500/200）",
        )
    )

    profile, why = read_runtime_profile(repo)
    checks.append(
        _check("runtime_profile", profile == EXPECTED_PROFILE, f"RUNTIME_PROFILE={profile!r} {why}")
    )

    gateway_code = next(
        (c for c in (_gateway_code(b) for b in bodies) if c in GATEWAY_DIAGNOSIS), ""
    )
    meaning, action = GATEWAY_DIAGNOSIS.get(gateway_code, ("", ""))

    return {
        "base_url": base_url,
        "checks": checks,
        "code_layer_ready": all(c["ok"] for c in checks),
        "gateway_diagnosis": {
            "code": gateway_code,
            "blocking": bool(gateway_code),
            "meaning": meaning,
            "action": action,
            "note": GATEWAY_NOTE if gateway_code else "",
        },
        "seed_layer_proven": False,
        "seed_layer_note": SEED_NOTE,
        "device_layer_proven": False,
        "device_layer_note": "本脚本不驱动设备；§15 demo 档走查由 IDE 原生 MCP 通道另行取证。",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="云端演示就绪自检（只读 GET，不写云端）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="云托管对外 HTTPS 地址")
    parser.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parents[2]),
        help="仓库根（用于读 miniapp/config/env.js）",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="单次 GET 超时秒数")
    parser.add_argument("--json", action="store_true", help="只输出 JSON（便于脚本/交接单引用）")
    args = parser.parse_args(argv)

    result = probe(args.base_url, Path(args.repo), args.timeout)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for c in result["checks"]:
            print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['evidence']}")
        diag = result["gateway_diagnosis"]
        if diag["blocking"]:
            print(f"\n⛔ 网关层阻塞：{diag['code']} —— {diag['meaning']}")
            print(f"   恢复动作：{diag['action']}")
            print(f"   {diag['note']}")
        print(f"\n代码层已部署：{'是' if result['code_layer_ready'] else '否'}")
        print(f"种子层已证：否 —— {SEED_NOTE}")
        print("设备层已证：否 —— 需 IDE 原生 MCP 通道的 §15 demo 档走查证据。")
    return 0 if result["code_layer_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
