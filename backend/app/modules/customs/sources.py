"""数据源登记表 —— 「数据源状态」这块牌子的唯一真相（D1 一等公民）。

## 为什么先于页面落地

决策文档 §3 的 Q-02 结论是「不接任何外部数据源」。这句话如果只写在文档里，页面就会
默认"有数据"，然后在演示现场说不清一条状态是哪来的。把来源状态做成**可查询的事实**：

* 每个来源一条：`state ∈ {available, unavailable}` + `reason`（为什么不available）；
* 页面据此渲染状态条；不可用的来源**不渲染查询入口**（PRD §3 第 4 条：不要放一个
  点进去必然失败的入口）；
* 官方源永远标 `unavailable`（直到拿到授权），人工登记标 `available`。

## ⭐ 守卫：开关与真实适配器必须同时存在（Q-02a 的实现层约束）

`CUSTOMS_OFFICIAL_SOURCE_ENABLED` **不是**"官能源可用"的开关，而是"官方源是否已被授权"
的陈述。在它之外还有第二个必要条件：`OFFICIAL_ADAPTERS` 里必须有**真实的适配器**。

只置环境变量、不加适配器的后果，此前是真实存在的：`/customs/sources` 会返回
`single_window.state=available`、`reason="已按授权接入（配置开启）"`，**而背后没有任何数据源** ——
这正是本项目最该防的"看起来有"，而且它恰好落在 Q-02a 的兑现机制上。

⇒ 判定式（可直接转成验收判据）：

    official_available == True  ⇒  该来源在 OFFICIAL_ADAPTERS 中有真实适配器

不成立时**不抛异常、不当 5xx**（那只会逼前端写失败页），而是：
`state` 保持 `unavailable`、`reason` 明写"配置已开启但无真实适配器（守卫拦截）"、
`notice` 标为配置违规、并记一条 `logger.warning`。宁可让演示现场少一条入口，
也不能让一条没有数据源的"可用"流出去。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SourceState = Literal["available", "unavailable"]

#: 来源清单（顺序＝页面展示顺序）。新增来源只加条目，不改调用方。
SOURCE_REGISTRY: tuple[dict[str, str], ...] = (
    {
        "key": "single_window",
        "label": "单一窗口 / 官方报关状态",
        "kind": "official",
        "reason": "未取得数据源授权（Q-02a 未决）；平台亦不具备报关企业资质（Q-01）",
    },
    {
        "key": "manifest",
        "label": "舱单（船代 / 港口系统）",
        "kind": "official",
        "reason": "未接入；首版靠人工登记舱单号与状态",
    },
    {
        "key": "manual",
        "label": "人工登记（报关行回执 / 口述）",
        "kind": "manual",
        "reason": "本版本唯一可用来源；每条事件带来源与获取时间",
    },
)

#: 真实官方源适配器登记表 —— `{来源 key: 适配器}`。**当前为空**：一个都还没接（Q-02a）。
#:
#: 它的存在只有一个目的：让「开关打开」不再等于「来源可用」。接入时在此登记
#: （值必须是能真实回源的调用，不能是 `lambda: None` 这类占位 —— 静态门禁会查）。
OFFICIAL_ADAPTERS: dict[str, Callable[..., Any]] = {}

#: 开关开了但没有适配器时，页面/接口要显示的那句话（不吞掉配置错误）
GUARD_REASON = "配置已开启但无真实适配器（守卫拦截：开关不能单独代表来源可用）"


def _official_enabled() -> bool:
    """官方源**授权**开关。默认关。"""
    return bool(getattr(get_settings(), "CUSTOMS_OFFICIAL_SOURCE_ENABLED", False))


def missing_adapters() -> list[str]:
    """登记为 official、但 `OFFICIAL_ADAPTERS` 里没有真实适配器的来源 key。"""
    return [
        entry["key"]
        for entry in SOURCE_REGISTRY
        if entry["kind"] == "official" and entry["key"] not in OFFICIAL_ADAPTERS
    ]


def official_ready() -> bool:
    """官方源是否**既被授权、又有真实适配器**。

    这就是 Q-02a 的判定式本体：`official_available == True ⇒ 存在真实适配器`。
    两者缺一，都返回 `False` —— 页面据此不渲染官方查询入口。
    """
    if not _official_enabled():
        return False
    return not missing_adapters()


def snapshot() -> dict[str, Any]:
    """返回所有来源的当前状态（页面数据源状态条直接消费）。"""
    enabled = _official_enabled()
    ready = official_ready()
    missing = missing_adapters()
    # 配置违规：开关开了，却至少有一个 official 来源没有适配器
    misconfigured = enabled and bool(missing)
    if misconfigured:
        logger.warning(
            "customs.sources: CUSTOMS_OFFICIAL_SOURCE_ENABLED=true 但缺少真实适配器 %s；"
            "按不可用处理（Q-02a 守卫）",
            ",".join(missing),
        )

    items: list[dict[str, Any]] = []
    for entry in SOURCE_REGISTRY:
        if entry["kind"] == "official":
            state: SourceState = "available" if ready else "unavailable"
            if ready:
                reason = "已按授权接入（配置开启 · 适配器已登记）"
            elif enabled:
                # 开关开了却没适配器：⛔ 不回落到 entry["reason"]（那是"未授权"的说法），
                # 必须把"配置违规"这件事本身说出来，否则运维会以为授权已生效
                reason = GUARD_REASON
            else:
                reason = entry["reason"]
        else:
            # 人工登记永远可用：它不依赖任何外部系统，这也是"断源仍可作业"的底气（AC-08）
            state = "available"
            reason = entry["reason"]
        items.append(
            {
                "key": entry["key"],
                "label": entry["label"],
                "kind": entry["kind"],
                "state": state,
                "state_label": "可用" if state == "available" else "暂不可用",
                "reason": reason,
            }
        )

    if misconfigured:
        notice = "官方来源配置异常（开关已开启但无真实适配器）：按不可用处理，状态仍来自人工登记"
    elif ready:
        notice = "官方来源已接入（授权与适配器均已登记）"
    else:
        notice = "本版本不接官方数据源：所有状态来自人工登记，以官方结果为准"

    return {
        "checked_at": datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        "official_available": ready,
        "official_enabled": enabled,
        "official_missing_adapters": missing,
        "sources": items,
        "notice": notice,
    }
