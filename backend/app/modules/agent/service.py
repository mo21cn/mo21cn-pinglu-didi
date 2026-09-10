"""货源解析 Agent 编排（F9）。

流程：自然语言 → LLM（结构化 JSON）→ pydantic 校验 + 港口/日期合法性过滤
→ AgentCall 审计落库 → 返回草稿（前端回填表单，用户确认后走 cargo API 创建）。

Agent 无直写（工程底线 2）：本模块不 import cargo.service，不写任何业务表
（除审计表 agent_calls）；确定性内核零接触。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.agent import AgentCall
from app.modules.agent import llm as llm_gateway
from app.modules.agent.llm import LLMError
from app.modules.agent.schemas import (
    AssistantResult,
    CargoParseResult,
    ContractDraftResult,
    ContractRisk,
    ParsedCargoField,
)
from app.modules.cargo.schemas import PORT_CODES

_DIGEST_LEN = 512

SYSTEM_PROMPT = """你是平陆运河"滴滴打船"平台的货源解析助手。任务：把货主的口语化描述解析为结构化货源草稿。

输出严格 JSON 对象，字段如下（信息缺失时置 null，不要编造）：
{
  "cargo_name": string|null,      // 货物名称，如"散装水泥"
  "cargo_type": string|null,      // 枚举: bulk(散货)/general(件杂货)/container(集装箱)/tanker(液货)/other
  "weight_t": number|null,        // 重量（吨）
  "origin_port": string|null,    // 起运港代码，只能是: NNG/GGU/WUZ/BIN/LZH/BSZ/CHZ/GXL/HEZ/YUL/QNZ/FCG/BHZ
  "dest_port": string|null,      // 目的港代码，同上
  "expect_date": string|null,    // 期望装货日期 "YYYY-MM-DD"（口语"下周三"等按今天推算）
  "offer_price": number|null,    // 运费出价（元；"2万5"→25000）
  "field_confidence": {           // 每个字段的置信度 0-1
    "cargo_name": number, "cargo_type": number, "weight_t": number,
    "origin_port": number, "dest_port": number, "expect_date": number, "offer_price": number
  }
}

规则：
1. 港口用"城市名→代码"映射：南宁→NNG、贵港→GGU、梧州→WUZ、来宾→BIN、柳州→LZH、百色→BSZ、
   崇左→CHZ、桂林→GXL、贺州→HEZ、玉林→YUL、钦州→QNZ、防城港→FCG、北海→BHZ。
2. 只输出 JSON，不要任何其他文字。
"""


class AgentServiceError(Exception):
    """Agent 服务错误（携带 kind 便于路由层映射 HTTP 状态）。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _sanitize(field: str, value, today: date) -> tuple[object, bool]:
    """单字段合法化过滤；返回 (清洗后值, 是否需人工复核)。"""
    if value is None:
        return None, True
    if field in ("origin_port", "dest_port"):
        ok = isinstance(value, str) and value in PORT_CODES
        return (value if ok else None), (not ok)
    if field == "expect_date":
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError:
            return None, True
        return (parsed if parsed >= today else None), (parsed < today)
    return value, False


async def parse_cargo(db: Session, *, user_id: int, text: str) -> CargoParseResult:
    """解析货源自然语言 → 草稿（含审计落库）。"""
    settings = get_settings()
    today = date.today()

    record = AgentCall(
        user_id=user_id,
        agent_name="cargo_parse",
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        prompt_digest=text[:_DIGEST_LEN],
    )

    try:
        result = await llm_gateway.chat_json(system=SYSTEM_PROMPT, user=text)
    except LLMError as exc:
        record.success = False
        record.error_kind = exc.kind
        record.response_digest = str(exc)[:_DIGEST_LEN]
        db.add(record)
        db.commit()
        raise AgentServiceError(exc.kind, str(exc)) from exc

    raw: dict = result.content
    field_conf: dict = raw.get("field_confidence") or {}

    cleaned: dict = {}
    needs_review: list[str] = []
    confidences: list[float] = []

    for field in (
        "cargo_name", "cargo_type", "weight_t",
        "origin_port", "dest_port", "expect_date", "offer_price",
    ):
        value, review = _sanitize(field, raw.get(field), today)
        if review:
            needs_review.append(field)
        confidences.append(float(field_conf.get(field) or (0.9 if value is not None and not review else 0.0)))
        cleaned[field] = value

    draft = ParsedCargoField(**cleaned)
    confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

    record.mocked = result.mocked
    record.latency_ms = result.latency_ms
    record.response_digest = result.raw[:_DIGEST_LEN]
    db.add(record)
    db.commit()

    return CargoParseResult(
        draft=draft,
        confidence=confidence,
        needs_review=needs_review,
        mocked=result.mocked,
        latency_ms=result.latency_ms,
    )


# ===========================================================================
# 客服导购 Agent（F10）：FAQ / 航线 / 用法咨询，纯读零直写
# 知识注入：MVP 阶段平台知识硬编码进 system prompt（后续替换点 → RAG 检索）
# ===========================================================================

ASSISTANT_SYSTEM_PROMPT = """你是平陆运河"滴滴打船"平台的智能客服助手，帮助用户解答平台用法、航线与业务咨询。

## 平台知识库
【三角色】货主（发货找船）/ 船东（接单找货，船舶需先提交审核，关键信息变更会自动降级重审）/ 港口方（泊位调度与船舶审核）。
【主流程】货主发布货源（可直接发布进撮合池）→ 智能撮合（硬约束过滤+评分排序，只推荐不出单）→ 货主选定下单 →
支付运费 → 船东启运 → 货主签收完成；撮合后任意一方可撤单（已付款自动全额退款）。
【支付】运费由货主支付，撤单自动退款；MVP 阶段为模拟支付，后续接入微信支付。
【航线港口（13 个）】内河：南宁 NNG（运河江海联运枢纽）、贵港 GGU（内河第一大港）、梧州 WUZ（东向大湾区门户）、
来宾 BIN、柳州 LZH、百色 BSZ、崇左 CHZ、桂林 GXL、贺州 HEZ、玉林 YUL；海港：钦州 QNZ、防城港 FCG、北海 BHZ（北部湾三港区）。
【船型与货类】散货 bulk（水泥/矿/煤/砂石/粮）、件杂货 general（钢材/设备）、集装箱 container、液货 tanker（油品/化工，
仅液货船可承运）、其他 other。
【撮合逻辑】硬约束（船舶已认证、证书覆盖装货期、载重足额、船型货类兼容）+ 评分（载重利用率/船型适配/船籍港就近/证书余量）。

## 回答规则
1. 只回答与平台/航运业务相关问题；无关问题礼貌引导回业务话题。
2. 事实以知识库为准，不确定的就说"建议咨询平台人工客服"，不要编造数字（运价行情等）。
3. 简明扼要，可用简短列表；中文回答，100-200 字为宜。
4. 纯咨询只读：不承诺代替用户执行任何写操作（下单/支付/审核），引导用户自行操作。
5. 输出格式：严格 JSON 对象 {"answer": "回答文本"}，answer 内可用换行符 \\n 组织列表。
"""


def _mock_answer(question: str) -> dict:
    """客服导购的 LLM_MOCK 规则模板（关键词匹配，CI 用）。"""
    q = question.lower()
    if any(k in q for k in ("发货", "发布货源", "怎么发")):
        answer = (
            "发货流程：进入「工作台-发布货源」，填写货物名称、货类、重量、起讫港、"
            "期望装货日期即可发布进入撮合池；也可用「智能填写」一句话描述，我帮你解析成草稿。"
        )
    elif any(k in q for k in ("找船", "撮合", "怎么匹配")):
        answer = (
            "智能撮合：发布货源后点「智能找船」，系统按硬约束（船舶认证/证书/载重/船型兼容）"
            "过滤并评分排序，货主选定候选船后下单。撮合只做推荐，出单由您确认。"
        )
    elif any(k in q for k in ("支付", "运费", "退款", "撤单")):
        answer = (
            "运费由货主在订单页支付（当前为模拟支付）；撮合后双方均可撤单，"
            "已支付的订单撤单将自动全额退款。"
        )
    elif any(k in q for k in ("港口", "航线", "哪些港")):
        answer = (
            "平台覆盖 13 个港口：内河——南宁/贵港/梧州/来宾/柳州/百色/崇左/桂林/贺州/玉林；"
            "海港——钦州/防城港/北海（北部湾三港区）。"
        )
    elif any(k in q for k in ("船型", "货类", "液货", "集装箱")):
        answer = (
            "货类分五种：散货（水泥/矿/煤）、件杂货（钢材/设备）、集装箱、液货（油品/化工，"
            "仅液货船可承运）、其他。船舶需先认证，关键信息变更会自动重审。"
        )
    else:
        answer = "您好，我是平台智能客服，可咨询发货、找船、支付、航线港口、船型货类等问题。"
    return {"answer": answer}


async def answer_question(
    db: Session, *, user_id: int, question: str, history: list[dict]
) -> AssistantResult:
    """客服导购问答（审计落库，纯读零直写）。"""
    settings = get_settings()

    record = AgentCall(
        user_id=user_id,
        agent_name="assistant",
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        prompt_digest=question[:_DIGEST_LEN],
    )

    # 多轮上下文拼接进 user 消息（chat_json 网关出口只有 system/user 两槽）
    context = "\n".join(
        f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')}"
        for m in history[-6:]
        if isinstance(m, dict) and m.get("content")
    )
    user_content = f"（最近对话，供参考）\n{context}\n\n当前问题：{question}" if context else question

    try:
        # 复用 chat_json：要求 LLM 以 {"answer": "..."} 结构化输出（统一网关出口）
        result = await llm_gateway.chat_json(
            system=ASSISTANT_SYSTEM_PROMPT,
            user=user_content,
            mock_content=_mock_answer,
        )
        answer = str(result.content.get("answer") or "").strip()
        if not answer:
            raise LLMError("bad_response", "LLM 输出缺少 answer 字段")
    except LLMError as exc:
        record.success = False
        record.error_kind = exc.kind
        record.response_digest = str(exc)[:_DIGEST_LEN]
        db.add(record)
        db.commit()
        raise AgentServiceError(exc.kind, str(exc)) from exc

    record.mocked = result.mocked
    record.latency_ms = result.latency_ms
    record.response_digest = answer[:_DIGEST_LEN]
    db.add(record)
    db.commit()

    return AssistantResult(
        answer=answer,
        mocked=result.mocked,
        latency_ms=result.latency_ms,
    )


# ===========================================================================
# 智能合同 Agent（F11）：订单 → 合同草稿 + 风险点提示（中风险场景）
# 安全设计：核心条款由 contract.py 确定性模板渲染（金额/日期/港口零 LLM），
# LLM 仅产出不可抗力/争议解决等补充条款文字（prompt 禁止具体数字日期）；
# 风险点 100% 由规则引擎产生。草稿不落库、不具法律效力。
# ===========================================================================

CONTRACT_SYSTEM_PROMPT = """你是内河航运运输合同的法务助理。任务：基于给定订单事实，起草合同的标准补充条款。

输出严格 JSON 对象：
{"supplementary_clauses": [{"title": "条款标题", "text": "条款正文"}]}

必须包含四条：1.不可抗力 2.违约与责任划分 3.争议解决 4.安全与环保责任。
规则：
1. 只写通用条款文字，**严禁出现任何具体金额、日期、港口名、船名、人名**（由主合同确定性条款承载）。
2. 条款符合中华人民共和国民法典及国内水路运输相关法规的一般原则。
3. 每条正文 50-120 字，专业、可执行。
4. 只输出 JSON。
"""


def _mock_contract_clauses(_: str) -> dict:
    """合同 Agent 的 LLM_MOCK 规则模板（固定标准条款，CI 用；编号由拼接层统一）。"""
    return {"supplementary_clauses": [
        {"title": "不可抗力", "text": "因洪水、大风、封航、政府管制等不可抗力导致无法履约的，受影响方应及时通知对方并提供证明，双方均免责；合同期限相应顺延或协商解除。"},
        {"title": "违约与责任划分", "text": "甲方逾期备货或乙方逾期到船的，按日向对方支付违约金；运输途中货物毁损、灭失由乙方承担赔偿责任，甲方自行申报的货物性质不实导致的损失除外。"},
        {"title": "争议解决", "text": "本合同履行发生争议的，双方应先行协商；协商不成的，提交平台调解或向合同签订地有管辖权的人民法院提起诉讼。"},
        {"title": "安全与环保责任", "text": "乙方应确保船舶适航、证书有效，遵守航道与港口安全管理规定；双方共同落实货物遮盖与污染防治要求，杜绝污染物排入水体。"},
    ]}


async def generate_contract(db: Session, *, user_id: int, order_id: int) -> ContractDraftResult:
    """订单 → 合同草稿 + 风险点（审计落库；Agent 无直写，草稿不落库）。"""
    from datetime import date as _date

    from sqlalchemy import select as _select

    from app.models.cargo import Cargo
    from app.models.order import Order
    from app.models.payment import Payment
    from app.models.ship import Ship
    from app.models.user import User
    from app.modules.agent import contract as contract_kernel

    settings = get_settings()

    record = AgentCall(
        user_id=user_id,
        agent_name="contract",
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        prompt_digest=f"order:{order_id}",
    )

    order = db.get(Order, order_id)
    if order is None:
        raise AgentServiceError("bad_request", "订单不存在")
    if user_id not in (order.shipper_id, order.owner_id):
        raise AgentServiceError("bad_request", "仅订单参与方可生成合同")
    if order.status == "cancelled":
        record.success = False
        record.error_kind = "bad_request"
        record.response_digest = "cancelled order"
        db.add(record)
        db.commit()
        raise AgentServiceError("bad_request", "订单已撤销，无法生成合同")

    cargo = db.get(Cargo, order.cargo_id)
    ship = db.get(Ship, order.ship_id)
    shipper = db.get(User, order.shipper_id)
    owner = db.get(User, order.owner_id)
    payment = db.execute(
        _select(Payment).where(Payment.order_id == order.id)
    ).scalar_one_or_none()
    if not all((cargo, ship, shipper, owner)):
        raise AgentServiceError("bad_request", "订单关联数据不完整")

    # ---- 确定性部分（零 LLM）：主体条款 + 风险规则 ----
    main_text = contract_kernel.render_contract(
        order, cargo, ship, shipper, owner, payment
    )
    risks = [
        ContractRisk(**r)
        for r in contract_kernel.check_risks(order, cargo, ship, payment, _date.today())
    ]

    # ---- LLM 部分：仅补充条款文字 ----
    risk_titles = "、".join(r.title for r in risks) or "无"
    fact_summary = (
        f"订单号 {order.id}，货类 {cargo.cargo_type}，重量 {float(cargo.weight_t)} 吨，"
        f"航线 {cargo.origin_port} 至 {cargo.dest_port}，状态 {order.status}，"
        f"运费 {'已锁定' if order.freight_price is not None else '面议未锁定'}，"
        f"已命中风险：{risk_titles}。请起草四条标准补充条款。"
    )
    try:
        result = await llm_gateway.chat_json(
            system=CONTRACT_SYSTEM_PROMPT,
            user=fact_summary,
            mock_content=_mock_contract_clauses,
        )
    except LLMError as exc:
        # LLM 故障降级：合同主体与风险仍可用确定性结果返回（补充条款置默认）
        record.success = False
        record.error_kind = exc.kind
        record.response_digest = str(exc)[:_DIGEST_LEN]
        db.add(record)
        db.commit()
        raise AgentServiceError(exc.kind, str(exc)) from exc

    clauses = result.content.get("supplementary_clauses") or []
    _cn_nums = "七八九十"
    supplement = "\n".join(
        f"## {_cn_nums[i] if i < len(_cn_nums) else i + 7}、{c.get('title', '')}"
        f"\n{c.get('text', '')}".strip()
        for i, c in enumerate(clauses)
        if isinstance(c, dict)
    )
    contract_text = main_text + (supplement + "\n" if supplement else "")
    contract_text += (
        "\n---\n*本草稿由平台智能合同 Agent 生成，核心条款来自订单数据，"
        "补充条款由 AI 起草；签署前请人工审核。*"
    )

    record.mocked = result.mocked
    record.latency_ms = result.latency_ms
    record.response_digest = contract_text[:_DIGEST_LEN]
    db.add(record)
    db.commit()

    return ContractDraftResult(
        order_id=order.id,
        contract_text=contract_text,
        risks=risks,
        mocked=result.mocked,
        latency_ms=result.latency_ms,
    )
