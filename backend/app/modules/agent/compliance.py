"""合规初筛内核（F17）：发布货源 / 船舶备案前的即时预检。

工程定位
--------
- **100% 确定性规则引擎、零 LLM**：与 ``contract.check_risks`` 同构——扫描业务
  事实产出结构化结论，可解释、可复现、可 CI 断言。合规判断不交给 LLM
  （工程底线 1：确定性内核不经 LLM）。
- **Agent 无直写（工程底线 2）**：预检只出结论与建议，**既不阻断也不代写**任何
  业务表；是否放行仍由业务域的 pydantic 校验与人工复核决定。前端拿到 ``block``
  时自行提示用户，而非由本模块拦截写入。
- **不含 OCR**：证照识别（营业执照/船舶检验证书的图片解析）是计划里独立的新
  依赖，本模块不引入；``screen_ship`` 的入参即备案表单的结构化字段，后续接
  OCR 时只需把识别结果并入同一 findings 结构。

规则总览
--------
货源侧：C1 禁运/管制货品（阻断）｜C2 危险货物申报与资质（提示）｜
        C3 集装箱航线适配（提示）｜C4 装货日期过近（提示）
船舶侧：S1 检验证书过期（阻断）｜S2 检验证书临期（提示）｜
        S3 主尺度比例异常（提示）｜S4 吃水需核实航道条件（提示）

边界说明：词表与阈值服务于平台 MVP 的「初筛」定位，用于把明显有问题的描述
挡在人工复核之前，**不构成执法或法律意见**；最终合规结论以主管机关规定为准。
"""
from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# 货类 / 港口常量（与业务域保持一致，避免重复维护）
# ---------------------------------------------------------------------------

# 集装箱干线港：集装箱货类建议起讫港至少一端为干线港，否则需中转
CONTAINER_LINE_PORTS = ("QNZ", "WUZ")

# 危险货物关键词：命中即提示需申报与专用资质（不阻断）
RESTRICTED_KEYWORDS: tuple[str, ...] = (
    "危险化学", "危化品", "易燃", "易爆", "剧毒", "腐蚀", "强酸", "强碱",
    "甲醇", "乙醇", "汽油", "柴油", "液化气", "天然气", "原油", "石油",
    "硫酸", "液碱", "液氨", "苯", "农药",
)

# 禁运 / 管制关键词：命中即阻断（平台不得承运，需引导用户走主管机关渠道）
FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "枪支", "弹药", "爆炸物", "烟花爆竹", "管制刀具",
    "毒品", "麻醉药品", "精神药品", "放射", "核材料", "铀",
    "固体废物", "洋垃圾", "危险废物", "医疗废物", "走私",
)

# 内河干线航道常见的通航吃水条件（米）：超过即提示核实，非硬性限制
DRAFT_REVIEW_THRESHOLD_M = 4.5


def _finding(
    code: str, severity: str, title: str, detail: str, suggestion: str
) -> dict[str, str]:
    """构造一条合规结论（dict 形态，由上层转 pydantic）。"""
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
    }


def _hit_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
    """返回文本中命中的关键词（保持词表顺序，去重）。"""
    lowered = text.lower()
    return [k for k in keywords if k.lower() in lowered]


def _summary(findings: list[dict[str, str]]) -> str:
    if not findings:
        return "未发现合规问题，可正常提交。"
    blocks = [f for f in findings if f["severity"] == "block"]
    warns = [f for f in findings if f["severity"] == "warn"]
    if blocks:
        return f"发现 {len(blocks)} 项阻断项、{len(warns)} 项提示，请修正后再提交。"
    return f"发现 {len(warns)} 项提示，建议核实后提交。"


def _level(findings: list[dict[str, str]]) -> str:
    if any(f["severity"] == "block" for f in findings):
        return "block"
    if any(f["severity"] == "warn" for f in findings):
        return "warn"
    return "pass"


# ---------------------------------------------------------------------------
# 货源侧
# ---------------------------------------------------------------------------


def screen_cargo(
    *,
    cargo_name: str,
    cargo_type: str,
    origin_port: str,
    dest_port: str,
    expect_date: date,
    remark: str = "",
    today: date | None = None,
) -> dict[str, object]:
    """货源合规初筛（C1–C4）。返回 {level, findings, checked_rules}。"""
    today = today or date.today()
    findings: list[dict[str, str]] = []
    haystack = f"{cargo_name} {remark}"

    # C1 禁运 / 管制货品（阻断）
    hits = _hit_keywords(haystack, FORBIDDEN_KEYWORDS)
    if hits:
        findings.append(_finding(
            "C1", "block", "疑似禁运/管制货品",
            f"货物描述命中管制词：{'、'.join(hits)}。此类货物平台不承接受理。",
            "请勿发布；如确属合法运输，请先取得主管机关许可并走线下合规渠道。",
        ))

    # C2 危险货物申报与资质（提示）
    danger = _hit_keywords(haystack, RESTRICTED_KEYWORDS)
    if danger and not hits:
        findings.append(_finding(
            "C2", "warn", "危险货物需申报与专用资质",
            f"货物描述命中危险货物词：{'、'.join(danger)}。内河运输危险货物需船舶持有"
            "相应适装证书、配备押运人员，并向海事管理机构办理申报。",
            "请确认承运船具备危险货物适装资质，并在备注中注明危险特性与应急措施。",
        ))
    if cargo_type == "tanker" and not danger and not hits:
        findings.append(_finding(
            "C2", "warn", "液货类需核实危险特性",
            "货类登记为液货，但货物描述中未见危险特性说明。液货是否属危险货物"
            "取决于具体品名与闪点。",
            "请在货物名称/备注中写明具体品名，必要时补充危险货物属性与包装方式。",
        ))

    # C3 集装箱航线适配（提示）
    if cargo_type == "container" and not (
        origin_port in CONTAINER_LINE_PORTS or dest_port in CONTAINER_LINE_PORTS
    ):
        findings.append(_finding(
            "C3", "warn", "集装箱航线适配待确认",
            f"起讫港为 {origin_port} → {dest_port}，两端均非集装箱干线港"
            f"（{'/'.join(CONTAINER_LINE_PORTS)}）。集装箱货类可能需要中转或拼箱。",
            "建议确认该航线是否有班期与箱源；无箱源时可考虑改为件杂货或改选干线港起讫。",
        ))

    # C4 装货日期过近（提示）
    days = (expect_date - today).days
    if days < 0:
        findings.append(_finding(
            "C4", "block", "期望装货日期已过",
            f"期望装货日期 {expect_date.isoformat()} 早于今日，发布后无法进入有效撮合。",
            "请重新选择不早于今日的装货日期。",
        ))
    elif days <= 1:
        findings.append(_finding(
            "C4", "warn", "装货日期过近",
            f"距期望装货日期仅 {days} 天，备货、订舱与泊位安排时间紧张，成单概率偏低。",
            "建议适当留出准备期，或在备注中说明可加急协调的泊位与设备。",
        ))

    return {"level": _level(findings), "findings": findings, "checked_rules": 4}


def screen_cargo_text(text: str) -> dict[str, object]:
    """仅依据自由文本做合规初筛（供 F20 统一入口的 compliance 意图复用）。

    文本形态拿不到结构化字段，故只做词表类规则（C1 / C2），
    返回结构与其他初筛一致，便于前端统一渲染。
    """
    findings: list[dict[str, str]] = []
    hits = _hit_keywords(text, FORBIDDEN_KEYWORDS)
    if hits:
        findings.append(_finding(
            "C1", "block", "疑似禁运/管制货品",
            f"描述命中管制词：{'、'.join(hits)}。此类货物平台不承接受理。",
            "请勿发布；如确属合法运输，请先取得主管机关许可并走线下合规渠道。",
        ))
    danger = _hit_keywords(text, RESTRICTED_KEYWORDS)
    if danger and not hits:
        findings.append(_finding(
            "C2", "warn", "危险货物需申报与专用资质",
            f"描述命中危险货物词：{'、'.join(danger)}。需船舶持有适装证书并向海事"
            "管理机构办理申报。",
            "请确认承运船具备危险货物适装资质，并在备注中注明危险特性与应急措施。",
        ))
    return {"level": _level(findings), "findings": findings, "checked_rules": 2}


# ---------------------------------------------------------------------------
# 船舶侧
# ---------------------------------------------------------------------------


def screen_ship(
    *,
    ship_name: str,
    ship_type: str,
    deadweight_t: float,
    length_m: float,
    width_m: float,
    draft_m: float,
    home_port: str,
    cert_no: str,
    cert_expiry: date,
    today: date | None = None,
) -> dict[str, object]:
    """船舶备案合规初筛（S1–S4）。返回 {level, findings, checked_rules}。"""
    today = today or date.today()
    findings: list[dict[str, str]] = []

    # S1/S2 检验证书有效期（备案入口的第一道闸）
    cert_days = (cert_expiry - today).days
    if cert_days < 0:
        findings.append(_finding(
            "S1", "block", "船舶检验证书已过期",
            f"证书 {cert_no} 已于 {cert_expiry.isoformat()} 到期，不具备合法承运资质。",
            "请换证后重新备案；在证书有效期内方可进入撮合池。",
        ))
    elif cert_days < 30:
        findings.append(_finding(
            "S2", "warn", "船舶检验证书临期",
            f"证书 {cert_no} 将于 {cert_expiry.isoformat()} 到期（剩 {cert_days} 天），"
            "备案通过后可能很快需要重新送审。",
            "建议尽快安排换证，避免在途履约期间证书失效。",
        ))

    # S3 主尺度比例异常（提示：常见内河货船长宽比约 4–8）
    if width_m > 0:
        ratio = length_m / width_m
        if ratio < 3 or ratio > 12:
            findings.append(_finding(
                "S3", "warn", "主尺度比例异常",
                f"船长 {length_m} 米 / 船宽 {width_m} 米 = {ratio:.1f}，"
                "超出内河货船常见长宽比（约 4–8），请核对填报是否有误。",
                "请复核船长、船宽填报值；如确为特种船型，请在备注中说明。",
            ))

    # S4 吃水需核实航道条件（提示）
    if draft_m > DRAFT_REVIEW_THRESHOLD_M:
        findings.append(_finding(
            "S4", "warn", "吃水需核实航道条件",
            f"满载吃水 {draft_m} 米，超过内河干线航道常见通航吃水（约 "
            f"{DRAFT_REVIEW_THRESHOLD_M} 米），需核实目标航线水深与桥梁净空。",
            "建议按航线官方通航通告核算载重能力，必要时减载运行。",
        ))

    # S5 船籍港缺失（提示：影响撮合的就近评分，非合规硬项）
    if not home_port:
        findings.append(_finding(
            "S5", "warn", "船籍港未填写",
            "未填写船籍港，撮合时无法计算「船籍港就近」得分，推荐排序会偏低。",
            "建议补填船籍港代码以提升撮合匹配度。",
        ))

    return {"level": _level(findings), "findings": findings, "checked_rules": 5}


def summarize(result: dict[str, object]) -> str:
    """从初筛结果中取摘要（供路由层复用）。"""
    findings = result.get("findings") or []
    assert isinstance(findings, list)
    return _summary(findings)
