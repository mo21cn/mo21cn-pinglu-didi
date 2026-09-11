"""智能合同 Agent 的确定性内核（F11）。

分工（安全设计核心）：
- **本模块 100% 确定性**：合同核心条款（甲乙方/货物/航线/运费金额/日期/
  船舶证书）由订单链数据模板渲染；风险点由规则引擎扫描订单事实产生。
  金额、日期、港口、主体**绝不来自 LLM**（工程底线 1 延伸）。
- **LLM 只负责**：不可抗力/争议解决等补充条款的标准文字（prompt 禁止
  输出任何具体数字与日期），供 service 层拼接到主体之后。
"""
from __future__ import annotations

from datetime import date

from app.models.cargo import Cargo
from app.models.order import Order
from app.models.payment import Payment
from app.models.ship import Ship
from app.models.user import User

PORT_NAMES = {
    "NNG": "南宁", "GGU": "贵港", "WUZ": "梧州", "BIN": "来宾",
    "LZH": "柳州", "BSZ": "百色", "CHZ": "崇左", "GXL": "桂林",
    "HEZ": "贺州", "YUL": "玉林", "QNZ": "钦州", "FCG": "防城港", "BHZ": "北海",
}

CARGO_TYPE_NAMES = {
    "bulk": "散货", "general": "件杂货", "container": "集装箱", "tanker": "液货", "other": "其他",
}

STATUS_NAMES = {
    "matched": "已撮合待承运", "shipped": "运输中", "completed": "已签收", "cancelled": "已撤销",
}

# 「大额运输」阈值（元）：运费达到即提示约定货物保险与责任限额（R7）。
# 与「高货值货类」取或：集装箱/液货即使运费不高，货值通常也较高。
LARGE_FREIGHT_THRESHOLD = 30000
HIGH_VALUE_CARGO_TYPES = ("container", "tanker")


def _party(user: User) -> str:
    return user.nickname or f"用户（ID：{user.id}）"


def render_contract(
    order: Order,
    cargo: Cargo,
    ship: Ship,
    shipper: User,
    owner: User,
    payment: Payment | None,
) -> str:
    """渲染合同主体（确定性，零 LLM）。"""
    origin = PORT_NAMES.get(cargo.origin_port, cargo.origin_port)
    dest = PORT_NAMES.get(cargo.dest_port, cargo.dest_port)
    price = "面议（以双方另行确认的金额为准）"
    if order.freight_price is not None:
        price = f"{float(order.freight_price):,.2f} 元（人民币）"
    pay_state = {
        "paid": "已支付（平台已收运费）",
        "refunded": "已退款",
        "closed": "已关闭（未支付）",
        "pending": "待支付",
    }.get(payment.status if payment else "", "未创建支付单（待支付）")

    return f"""# 内河货物运输合同（草稿）

> 本合同由"滴滴打船"平台根据订单 {order.id} 自动生成草稿，**不具法律效力**，
> 仅供双方核对；正式合同以线下签署或后续电子签章版本为准。

## 一、合同双方
- **甲方（托运人/货主）**：{_party(shipper)}
- **乙方（承运人/船东）**：{_party(owner)}

## 二、货物
- 货物名称：{cargo.cargo_name}
- 货类：{CARGO_TYPE_NAMES.get(cargo.cargo_type, cargo.cargo_type)}
- 重量：{float(cargo.weight_t):,.2f} 吨
- 备注：{cargo.remark or '无'}

## 三、运输航线与期限
- 航线：{origin}（{cargo.origin_port}） → {dest}（{cargo.dest_port}）
- 期望装货日期：{cargo.expect_date.isoformat()}
- 订单状态：{STATUS_NAMES.get(order.status, order.status)}（成交时间 {order.matched_at:%Y-%m-%d}）

## 四、承运船舶
- 船名：{ship.ship_name}
- 船型：{CARGO_TYPE_NAMES.get(ship.ship_type, ship.ship_type)}船
- 载重吨：{float(ship.deadweight_t):,.2f} 吨
- 船舶检验证书号：{ship.cert_no}（有效期至 {ship.cert_expiry.isoformat()}）

## 五、运费与支付
- 成交运费：{price}
- 支付状态：{pay_state}

## 六、签收与完结
货物运抵目的港后，甲方（或其书面委托的收货人）应当场验收；验收无异议视为签收，
订单转为"已签收"完结。货物有异议的，双方按平台留痕记录协商处理。
"""


def check_risks(
    order: Order,
    cargo: Cargo,
    ship: Ship,
    payment: Payment | None,
    today: date,
) -> list[dict[str, str]]:
    """确定性风险规则引擎（扫描订单事实，零 LLM）。"""
    risks: list[dict[str, str]] = []

    # R1 未支付就承运（资金风险）
    if order.status in ("matched", "shipped") and (payment is None or payment.status == "pending"):
        risks.append({
            "severity": "high",
            "title": "运费未支付",
            "detail": "订单处于承运流程但运费尚未支付，先运后付存在资金回收风险。",
            "suggestion": "建议在合同中约定'启运前付清运费'，并在支付完成后再行启运。",
        })

    # R2 面议未锁价
    if order.freight_price is None:
        risks.append({
            "severity": "high",
            "title": "运费未锁定",
            "detail": "订单成交价为面议，合同无确定金额条款，易产生结算纠纷。",
            "suggestion": "建议双方先议定运费并回填订单，再生成正式合同。",
        })

    # R3 装货日期临近
    days_to_load = (cargo.expect_date - today).days
    if days_to_load < 0:
        risks.append({
            "severity": "high",
            "title": "装货日期已过",
            "detail": f"期望装货日期 {cargo.expect_date.isoformat()} 早于今日，合同期限条款需重签。",
            "suggestion": "建议双方协商变更装货日期后再生成合同。",
        })
    elif days_to_load < 3:
        risks.append({
            "severity": "medium",
            "title": "装货日期临近",
            "detail": f"距期望装货日期仅 {days_to_load} 天，备货与泊位安排时间紧张。",
            "suggestion": "建议在合同中明确逾期装货的责任划分与顺延机制。",
        })

    # R4 船舶证书临期
    cert_days = (ship.cert_expiry - today).days
    if cert_days < 0:
        risks.append({
            "severity": "high",
            "title": "船舶检验证书过期",
            "detail": f"承运船舶证书已于 {ship.cert_expiry.isoformat()} 到期，不具备合法承运资质。",
            "suggestion": "建议立即停止履约，待船舶换证并重新通过平台审核。",
        })
    elif cert_days < 30:
        risks.append({
            "severity": "medium",
            "title": "船舶证书临期",
            "detail": f"承运船舶证书将于 {ship.cert_expiry.isoformat()} 到期（{cert_days} 天）。",
            "suggestion": "建议约定证书到期换证不影响在途履约的责任安排。",
        })

    # R5 液货危险品
    if cargo.cargo_type == "tanker":
        risks.append({
            "severity": "medium",
            "title": "液货/危险品运输",
            "detail": "液货类货物可能涉及危险品，承运资质与保险要求高于普货。",
            "suggestion": "建议在合同中补充危险品申报、专用船舶资质与货物保险条款。",
        })

    # ---- 商务条款完备性（R6–R9）：对齐验收口径「滞期费/违约金/保险/不可抗力」 ----
    # 说明：平台内置标准条款只覆盖「不可抗力 / 违约与责任划分 / 争议解决 /
    # 安全与环保」四项，且**均为定性表述**；下列规则把「条款缺失或未量化」这一
    # 确定性事实与订单事实结合，只在风险现实存在时提示，避免对每单都刷屏。
    # 已签收/已撤销的订单不再有条款完备性意义，故 R6/R7 只在履约进行中提示。

    active = order.status in ("matched", "shipped")

    # R6 滞期费/装卸时间未约定（散货、液货装卸作业耗时长，超时即产生滞期）
    if active and cargo.cargo_type in ("bulk", "tanker"):
        risks.append({
            "severity": "medium",
            "title": "滞期费未约定",
            "detail": "散货/液货装卸作业耗时长，遇天气、泊位或设备原因易超出约定装卸时间；"
                      "平台标准条款未包含滞期费标准。",
            "suggestion": "建议在合同中补充装卸时限与滞期费标准（如超时按日计费）及停泊待时归属。",
        })

    # R7 货物保险未约定（高货值货类，或大额运费合同）
    freight = float(order.freight_price) if order.freight_price is not None else None
    insurance_signal = ""
    if cargo.cargo_type in HIGH_VALUE_CARGO_TYPES:
        insurance_signal = f"{CARGO_TYPE_NAMES.get(cargo.cargo_type, cargo.cargo_type)}类货物货值通常较高"
    elif freight is not None and freight >= LARGE_FREIGHT_THRESHOLD:
        insurance_signal = f"本单运费 {freight:,.0f} 元属大额运输"
    if active and insurance_signal:
        risks.append({
            "severity": "medium",
            "title": "货物保险未约定",
            "detail": f"{insurance_signal}；平台标准条款仅约定承运人赔偿责任，未涉及投保义务。",
            "suggestion": "建议补充货主投保义务或承运人责任险条款，并明确免赔与理赔流程。",
        })

    # R8 违约金标准未量化（临近装货、双方均可能逾期时才有现实意义）
    if order.status == "matched" and 0 <= days_to_load <= 7:
        risks.append({
            "severity": "low",
            "title": "违约金标准未量化",
            "detail": "标准条款仅约定「按日支付违约金」，未约定计算基数与比例，"
                      "临近装货期时双方逾期风险上升但索赔口径不明确。",
            "suggestion": "建议明确违约金计算基数、比例上限与免责情形，避免结算时争议。",
        })

    # R9 在途不可抗力风险（货物已启运，汛期水位/大风封航等直接影响履约）
    if order.status == "shipped":
        risks.append({
            "severity": "low",
            "title": "在途不可抗力风险",
            "detail": "货物已处运输途中，汛期水位变化、大风封航或航道管制可能造成延误或改道；"
                      "标准条款未约定通知时限与举证责任。",
            "suggestion": "建议明确不可抗力发生后的通知时限、证明材料与期限顺延/解约的处理方式。",
        })

    return risks
