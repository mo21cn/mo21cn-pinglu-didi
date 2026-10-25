"""数据源登记表 —— 「数据源状态」这块牌子的唯一真相（D1 一等公民）。

## 为什么先于页面落地

决策文档 §3 的 Q-02 结论是「不接任何外部数据源」。这句话如果只写在文档里，页面就会
默认"有数据"，然后在演示现场说不清一条状态是哪来的。把来源状态做成**可查询的事实**：

* 每个来源一条：`state ∈ {available, unavailable}` + `reason`（为什么不available）；
* 页面据此渲染状态条；不可用的来源**不渲染查询入口**（PRD §3 第 4 条：不要放一个
  点进去必然失败的入口）；
* 官方源永远标 `unavailable`（直到拿到授权），人工登记标 `available`。

`state` 由**配置**决定而不是由"代码里有没有实现"决定 —— 拿到授权后只改这里，页面不用动。
环境变量 `CUSTOMS_OFFICIAL_SOURCE_ENABLED=true` 是唯一开关（默认关闭）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from app.core.config import get_settings

SourceState = Literal["available", "unavailable"]

#: 来源清单（顺序＝页面展示顺序）。新增来源只加条目，不改调用方。
SOURCE_REGISTRY: tuple[dict[str, str], ...] = (
    {
        "key": "single_window",
        "label": "单一窗口 / 官方报关状态",
        "kind": "official",
        "reason": "未取得数据源授权（Q-02 未决）；平台亦不具备报关企业资质（Q-01）",
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


def _official_enabled() -> bool:
    """官方源开关。默认关；即使打开，也只影响"是否允许出现 official 事件"。"""
    return bool(getattr(get_settings(), "CUSTOMS_OFFICIAL_SOURCE_ENABLED", False))


def snapshot() -> dict[str, Any]:
    """返回所有来源的当前状态（页面数据源状态条直接消费）。"""
    official_ok = _official_enabled()
    items: list[dict[str, Any]] = []
    for entry in SOURCE_REGISTRY:
        if entry["kind"] == "official":
            state: SourceState = "available" if official_ok else "unavailable"
            reason = entry["reason"] if not official_ok else "已按授权接入（配置开启）"
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

    return {
        "checked_at": datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        "official_available": official_ok,
        "sources": items,
        "notice": (
            "本版本不接官方数据源：所有状态来自人工登记，以官方结果为准"
            if not official_ok
            else "官方来源已开启"
        ),
    }
