"""S3 合同派生 —— 从**已接受事实**生成结构化合同草稿（BP-03 第 8 条 / D1-08 / §10.1 第 7 步）。

合同原文（逐字）
----------------
* BP-03 第 8 条：`Generate one structured contract draft/template from the accepted
  facts and approved scope.`
* 包边界：`One domestic contract template is sufficient.` ⇒ **模板是常量**，
  不需要模板设计器；真正要证明的是**派生关系**。
* D1-08：`Contract draft derives from accepted facts; evidence and status remain
  distinct from live e-signature`，判据是 `Contract version and linked evidence inspection`。
* §10.1 第 7 步：`Create the contract and record labeled sample signature evidence.`

本模块的立场：**"派生"与"编造"只差一件事 —— 每个字段有没有来源行。**
所以 `derive_contract` 在写库前跑一条不变式：合同 payload 里**每一个**出现的字段路径，
都必须在字段来源表里有对应的行；缺一个就**中止**（不产生"看起来完整"的合同）。
这条不变式不是文档里的承诺，是代码里的闸门 —— 将来有人往 payload 里加字段却忘了登记来源，
它会当场炸，而不是安静地多出一句无出处的合同条款。

为什么输入是**发布记录**而不是"成果当前版本"
--------------------------------------------
必须点名"客户**当时接受的那一版**"。用成果当前版本会得到"按现在的规则重算"的结果：
报价被编辑过一次，重算出的合同金额就与客户接受的不是同一个数，而记录上仍写着
"基于客户接受" —— 这正是裁定三冻结细节②要防的（已接受的旧版本永久保留）。
所以本模块只读 `ent_offer_release.snapshot_json`（发布那一刻冻结的客户投影）
与 `ent_offer_response`（接受事实），**不读**任何会被后续编辑改写的当前值。

三类前置，各自给不同的状态码
----------------------------
| 情形 | 为什么 |
| --- | --- |
| 发布不存在 | 404（不区分"不存在"与"无权知晓"） |
| 发布**还没有**客户响应 | 409 —— 未响应的报价不是"已接受事实" |
| 客户响应是**拒绝** | 409 —— 拒绝不能派生合同 |
| 被接受的不是**对客报价** | 400 —— `contract_review` 也在客户可见白名单里、也会被发布被响应，但对"合同核对稿"的接受不是"客户接受了商业承诺"，据此派生第二份合同是错的 |
| 冻结快照里没有内容 | 400 —— 派生出的合同会是一份空壳，宁可在写入路径上炸 |
| 同一份已接受事实**已派生过** | 409（并回已存在的那份合同 id）—— 判据是 DB 的 `UNIQUE(release_id)`，不是"先查后写" |

事务：为什么不用 `artifacts.create_artifact`
-------------------------------------------
`create_artifact` **内部自己 commit**（被很多路径复用）。复用它会变成两次提交：
"先提交合同成果、再提交派生记录"。中途失败就留下**一份没有派生记录的合同草稿** ——
而"这份合同从哪来"正是本切片的全部价值，一份查不到出处的合同比没有合同更糟
（它会被当真）。⇒ 本模块自己在一个事务里写四张表，最后一次性提交；
注册表的字段契约校验照旧（`registry.validate_payload`），没有绕过。

产物来源标成 `manual` 的理由
----------------------------
`artifacts` 只有 `agent` / `manual` 两态。派生**不是模型产出**（没有"模型声明过什么来源"
这回事），所以是 `manual`。它的可核对性由**字段来源表**承担 —— 比 `ent_offer_source_check`
更具体（那里是"人核过没有"，这里是"这个值从哪来"）。
⚠️ 这也意味着发布派生出合同时，`offers.source_gate` 会判"人工直写 ⇒ 无待核验项 ⇒ 放行" ——
**这是对的，不是漏洞**：合同不需要"核验模型的来源声明"，它需要的是逐字段来源可查，
而那由本模块自己的不变式与 `ent_contract_field_source` 保证。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.entrust import artifacts as art
from app.modules.entrust import offers as offers_svc
from app.modules.entrust import plan as plan_svc
from app.modules.entrust import registry as reg
from app.modules.entrust.authz import load_assignment

# ── 模板（BP-03 包边界：一份国内运输模板即可）──────────────────────────────

TEMPLATE_CODE = "domestic-transport"
TEMPLATE_VERSION = "v1"
TEMPLATE_REF = f"{TEMPLATE_CODE}:{TEMPLATE_VERSION}"

CONTRACT_TYPE = "contract_review"

#: 只有这一种成果类型的"接受"才构成 BP-03 意义上的**已接受报价事实**。
#: `contract_review` 同样在 `registry.CUSTOMER_VISIBLE_TYPES` 里（它也会被发布、被响应），
#: 但对"合同核对稿"的接受 ≠ 客户接受了商业承诺 —— 用它派生第二份合同是**语义错位**，
#: 而且会形成"合同派生合同"的链，D1-08 要的"从已接受事实派生"就断了。
ACCEPTABLE_QUOTE_TYPE = "customer_quote"

# ── 字段来源类别（与迁移注释同一取值域）────────────────────────────────────

SOURCE_ACCEPTED_RELEASE = "accepted_release"
SOURCE_CUSTOMER_RESPONSE = "customer_response"
SOURCE_ASSIGNMENT = "assignment"
SOURCE_LEG = "leg"
SOURCE_ORGANIZATION = "organization"
SOURCE_TEMPLATE = "template"
ALL_SOURCE_KINDS = frozenset(
    {
        SOURCE_ACCEPTED_RELEASE,
        SOURCE_CUSTOMER_RESPONSE,
        SOURCE_ASSIGNMENT,
        SOURCE_LEG,
        SOURCE_ORGANIZATION,
        SOURCE_TEMPLATE,
    }
)

#: 来源类别标签（**唯一**实现，与 `EVIDENCE_KIND_LABELS` 同一条纪律）。
#: 界面上那张来源表要显示"来自已接受报价"而不是 `accepted_release`；让前端自己
#: 存一份 ⇒ 改一处、另一处静默显示旧说法。所以由投影层把文案一起给出。
SOURCE_KIND_LABELS = {
    SOURCE_ACCEPTED_RELEASE: "已接受报价",
    SOURCE_CUSTOMER_RESPONSE: "客户响应",
    SOURCE_ASSIGNMENT: "委托单",
    SOURCE_LEG: "航段",
    SOURCE_ORGANIZATION: "组织",
    SOURCE_TEMPLATE: "模板",
}

#: 合同**必须**登记的字段路径前缀 —— 由注册表与本模块共同决定，测试对它做全覆盖断言。
#: ⚠️ 运输方式标签表**不再**在这里存一份：唯一实现是 `plan.MODE_LABELS`
#: （全仓两处各存一份 ⇒ 改一处、另一处静默留下旧口径）。取标签统一走 `plan.mode_label`。

#: 冻结快照里参与派生、且需要合同给出对应内容的报价字段。
#: 用来回答"这份合同少写了什么"（缺失项**列出来**，不编造默认值）。
_CONTRACTED_QUOTE_KEYS = ("amount", "currency", "includes", "excludes", "valid_until", "note")


class ContractError(RuntimeError):
    """合同派生的基础异常。HTTP 层按语义转 4xx（默认 400）。"""


class ContractNotFoundError(ContractError):
    """发布记录 / 派生记录 / 合同成果不存在。HTTP 层应转 404。"""


class ContractStateError(ContractError):
    """当前事实不允许派生（未响应 / 已拒绝 / 已派生过）。HTTP 层应转 409。

    `existing_contract_artifact_id`：已派生过时**带上那份合同的 id** ——
    "已经有一份了"是一句没用的拒绝，客户端需要能直接去读它。
    """

    def __init__(self, message: str, *, existing_contract_artifact_id: int | None = None) -> None:
        super().__init__(message)
        self.existing_contract_artifact_id = existing_contract_artifact_id


def utcnow_naive() -> datetime:
    """当前 UTC 朴素时间（与 ent_ 表时间字段存储格式一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


_FMT = "%Y-%m-%d %H:%M:%S"


def _fmt(dt: Any) -> str:
    """与 `offers._fmt` **逐字一致**：同一函数要同时接受 `datetime`（写入路径）
    与已格式化的 `str`（SQLite 把 DATETIME 列读回来就是字符串）。

    两边各写一份的代价是真实的：函数签名不同就会在"读回来"的那条路上炸
    （`'str' object has no attribute 'strftime'`），而写入路径全绿。
    """
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return str(dt.strftime(_FMT))


def _plain(value: Any) -> str:
    """把值写成**文本形式**（字段来源表的 `value_text`）。

    保持"与写进合同的字面一致"：来源表要能回答"合同上那个数是从哪来的"，
    若两边格式不同（`950` vs `950.0`），核对的人就得自己解释差异。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _numeric(value: Any) -> float | None:
    """能当作数读出来才返回数，否则 `None`。

    ⛔ **不**用"看起来像就返回原值"的宽松写法：`bool` 在 Python 里是 `int` 的子类，
    不挡住它会把 `True` 当成金额 1。这里的返回只用于**判定能不能写进价款条款**。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# ── 读 ──────────────────────────────────────────────────────────────────────

_DERIVATION_COLS = (
    "id, assignment_id, entrustment_id, release_id, response_id, quote_artifact_id, "
    "quote_revision_id, quote_revision_no, contract_artifact_id, contract_revision_id, "
    "contract_revision_no, template_code, template_version, effective_date, derived_by, "
    "derived_at, note"
)


def _row_to_derivation(row: Any) -> dict[str, Any]:
    return {
        "derivation_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "entrustment_id": (
            int(row["entrustment_id"]) if row["entrustment_id"] is not None else None
        ),
        "release_id": int(row["release_id"]),
        "response_id": int(row["response_id"]),
        "quote_artifact_id": int(row["quote_artifact_id"]),
        "quote_revision_id": int(row["quote_revision_id"]),
        "quote_revision_no": int(row["quote_revision_no"]),
        "contract_artifact_id": int(row["contract_artifact_id"]),
        "contract_revision_id": int(row["contract_revision_id"]),
        "contract_revision_no": int(row["contract_revision_no"]),
        "template_code": str(row["template_code"]),
        "template_version": str(row["template_version"]),
        "effective_date": row["effective_date"],
        "derived_by": int(row["derived_by"]),
        "derived_at": _fmt(row["derived_at"]),
        "note": row["note"],
    }


def get_derivation_by_release(session: Session, *, release_id: int) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(f"SELECT {_DERIVATION_COLS} FROM ent_contract_derivation WHERE release_id = :rid"),
            {"rid": release_id},
        )
        .mappings()
        .first()
    )
    return _row_to_derivation(row) if row is not None else None


def get_derivation_by_contract(
    session: Session, *, contract_artifact_id: int
) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                f"SELECT {_DERIVATION_COLS} FROM ent_contract_derivation "
                "WHERE contract_artifact_id = :aid ORDER BY id DESC"
            ),
            {"aid": contract_artifact_id},
        )
        .mappings()
        .first()
    )
    return _row_to_derivation(row) if row is not None else None


def list_field_sources(session: Session, *, derivation_id: int) -> list[dict[str, Any]]:
    """逐字段来源（**可核对**的落点）。按字段路径排序，顺序即契约。"""
    rows = session.execute(
        text(
            "SELECT id, derivation_id, field_path, value_text, source_kind, source_ref, created_at "
            "FROM ent_contract_field_source WHERE derivation_id = :did "
            "ORDER BY field_path, source_kind, source_ref"
        ),
        {"did": derivation_id},
    ).mappings()
    out = []
    for r in rows:
        kind = str(r["source_kind"])
        out.append(
            {
                "field_path": str(r["field_path"]),
                "value_text": str(r["value_text"]),
                # 原值与文案**同时**给出（与航段 `mode`/`mode_label` 同一取向）：
                # 界面显示文案、比对用原值。只给文案的话，"这两个取值其实不同"
                # 在库里就再也看不出来。
                "source_kind": kind,
                "source_kind_text": SOURCE_KIND_LABELS.get(kind, kind),
                "source_ref": str(r["source_ref"]),
            }
        )
    return out


# ── 纯计算：合同内容与逐字段来源 ────────────────────────────────────────────


def _org_name(session: Session, org_id: int | None) -> str | None:
    if org_id is None:
        return None
    row = (
        session.execute(text("SELECT name FROM ent_organization WHERE id = :oid"), {"oid": org_id})
        .mappings()
        .first()
    )
    return str(row["name"]) if row is not None else None


def _list_legs(session: Session, assignment_id: int) -> list[dict[str, Any]]:
    """⚠️ 委托给 `plan.list_legs` —— 航段是**计划**这一域的事实，不在这里再写一份 SQL。"""
    return plan_svc.list_legs(session, assignment_id)


def _enumerate_field_paths(payload: dict[str, Any]) -> set[str]:
    """列出 payload 里出现的**全部字段路径**（不变式的判据）。

    规则与 `_build` 的写法一一对应：
    * 标量/字符串顶层字段 ⇒ `key`；
    * 列表里每个元素是对象 ⇒ `key[i].<子键>`；元素是标量 ⇒ `key[i]`。
    """
    paths: set[str] = set()
    for key, value in payload.items():
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    paths.update(f"{key}[{i}].{sub}" for sub in item)
                else:
                    paths.add(f"{key}[{i}]")
        else:
            paths.add(key)
    return paths


def _build(
    session: Session,
    *,
    release: dict[str, Any],
    response: dict[str, Any],
    assignment: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any]]:
    """算出合同 payload + 逐字段来源。**不落库** —— 便于单测与人工复核。

    Returns:
        `(payload, sources, meta)`；`meta` 里是"这份合同少写了什么"的如实清单。
    """
    snapshot = release["snapshot"] or {}
    quote = snapshot.get("payload") or {}
    assignment_id = int(assignment["id"])
    owner_user_id = int(assignment["owner_user_id"])
    org_id = int(assignment["org_id"]) if assignment["org_id"] is not None else None
    release_ref = f"release:{release['release_id']}@v{release['revision_no']}"
    response_ref = f"response:{response['response_id']}"

    sources: list[dict[str, str]] = []

    def add(path: str, value: Any, kind: str, ref: str) -> None:
        sources.append(
            {
                "field_path": path,
                "value_text": _plain(value),
                "source_kind": kind,
                "source_ref": ref,
            }
        )

    # ① 当事方：货主来自委托单归属，承运方来自组织 —— 都取自**受理时已确定**的事实，
    #    不取"现在谁是经理"这类会漂移的值。
    shipper_name = f"货主用户 #{owner_user_id}"
    carrier_name = _org_name(session, org_id) or (
        f"承接组织 #{org_id}" if org_id is not None else "承接组织（未指定）"
    )
    parties = [
        {"role": "shipper", "name": shipper_name},
        {"role": "carrier", "name": carrier_name},
    ]
    add("parties[0].role", "shipper", SOURCE_TEMPLATE, TEMPLATE_REF)
    add("parties[0].name", shipper_name, SOURCE_ASSIGNMENT, f"assignment:{assignment_id}")
    add("parties[1].role", "carrier", SOURCE_TEMPLATE, TEMPLATE_REF)
    if org_id is not None:
        add("parties[1].name", carrier_name, SOURCE_ORGANIZATION, f"org:{org_id}")
    else:
        # 组织未定 ⇒ 承运方名称没有事实来源。**不编造**，登记为模板占位并如实标出来。
        add("parties[1].name", carrier_name, SOURCE_TEMPLATE, TEMPLATE_REF)

    # ② 条款：每一条都点名它的来源。被接受的报价内容一律取自**冻结快照**（release_ref），
    #    绝不回头读成果的当前版本。
    clauses: list[dict[str, str]] = []

    def clause(key: str, value_text: str, kind: str, ref: str) -> None:
        idx = len(clauses)
        clauses.append({"key": key, "text": value_text})
        add(f"clauses[{idx}].key", key, SOURCE_TEMPLATE, TEMPLATE_REF)
        add(f"clauses[{idx}].text", value_text, kind, ref)

    legs = _list_legs(session, assignment_id)
    if legs:
        route_text = "；".join(
            f"{plan_svc.mode_label(leg['mode'])} {leg['from_name']}→{leg['to_name']}"
            for leg in legs
        )
        idx = len(clauses)
        clauses.append({"key": "route_scope", "text": route_text})
        add(f"clauses[{idx}].key", "route_scope", SOURCE_TEMPLATE, TEMPLATE_REF)
        # ⚠️ 一条条款由**多个航段**构成 ⇒ 同一 field_path 有多条来源行。
        #    这正是唯一键包含 (source_kind, source_ref) 的原因：按 field_path 去重会把
        #    "另一段航段也在里面"这条事实丢掉。
        for leg in legs:
            add(f"clauses[{idx}].text", route_text, SOURCE_LEG, f"leg:{leg['leg_id']}")
    else:
        scope_text = f"运输范围以委托单 #{assignment_id} 的已受理范围为准"
        clause("route_scope", scope_text, SOURCE_ASSIGNMENT, f"assignment:{assignment_id}")

    absent: list[str] = []
    amount = quote.get("amount")
    currency = quote.get("currency")
    # ⚠️ 金额必须真的是个数才写进合同。合同把"金额正确性"列为不可省项，
    #    而冻结快照里 `amount` 完全可能是一句人写的 "面议" —— 那种值写进合同价款条款，
    #    会让下游（结算、对账）拿到一个**看起来是金额的字符串**。判不出来就当缺项，
    #    如实进缺失清单，**不替业务把"面议"解释成某个数**。
    if amount is not None and _numeric(amount) is not None:
        amount_text = f"合同价款 {_plain(amount)} {_plain(currency) if currency else ''}".strip()
        clause("amount", amount_text, SOURCE_ACCEPTED_RELEASE, release_ref)
    else:
        absent.append("amount")
    if not currency:
        absent.append("currency")

    includes = quote.get("includes") or []
    if includes:
        clause(
            "includes",
            "费用包含：" + "、".join(_plain(x) for x in includes),
            SOURCE_ACCEPTED_RELEASE,
            release_ref,
        )
    else:
        absent.append("includes")

    excludes = quote.get("excludes") or []
    if excludes:
        clause(
            "excludes",
            "费用不含：" + "、".join(_plain(x) for x in excludes),
            SOURCE_ACCEPTED_RELEASE,
            release_ref,
        )
    else:
        absent.append("excludes")

    valid_until = quote.get("valid_until")
    if valid_until:
        clause(
            "validity",
            f"报价有效期至 {_plain(valid_until)}",
            SOURCE_ACCEPTED_RELEASE,
            release_ref,
        )
    else:
        absent.append("valid_until")

    quote_note = quote.get("note")
    if quote_note:
        clause("quote_note", _plain(quote_note), SOURCE_ACCEPTED_RELEASE, release_ref)
    else:
        absent.append("note")

    # ③ 接受事实本身写成条款：合同的效力起点是"客户接受了**那一版**"。
    accepted_at = str(response["responded_at"])
    clause(
        "acceptance",
        f"客户已于 {accepted_at} 接受报价版本 v{release['revision_no']}",
        SOURCE_CUSTOMER_RESPONSE,
        response_ref,
    )

    # ④ 生效日：取自客户接受时间（业务事实），不是"生成时间"。
    effective_date = accepted_at[:10] if len(accepted_at) >= 10 else None
    if effective_date:
        add("effective_date", effective_date, SOURCE_CUSTOMER_RESPONSE, response_ref)

    # ⑤ 常驻声明：模板的免责声明 —— 它是**模板常量**，不是从业务事实推出来的。
    #    合同 §3.2 / D1-08 要求"证据与状态不得等同于实时电子签"，这句话必须常驻正文。
    note_text = (
        f"本核对稿由已接受的对客报价派生（模板 {TEMPLATE_REF}）；"
        "签署证据按样件模式标注（labeled_sample），不构成实时电子签署。"
    )
    add("note", note_text, SOURCE_TEMPLATE, TEMPLATE_REF)

    payload: dict[str, Any] = {"parties": parties, "clauses": clauses, "note": note_text}
    if effective_date:
        # ⚠️ 只有拿得到才写这个键。写成 `None` 会让不变式把"字段存在但没来源"报出来 ——
        #    而这里的正解是**不放这个字段**，不是给它编一个来源：`effective_date` 的唯一
        #    合法来源就是客户接受时间，没有它就是没有。
        payload["effective_date"] = effective_date
    meta = {"absent_quote_fields": absent, "leg_count": len(legs)}
    return payload, sources, meta


def _assert_every_field_has_source(payload: dict[str, Any], sources: list[dict[str, str]]) -> None:
    """**闸门**：合同里每个字段路径都必须有来源行，否则中止。

    没有这道闸门，"派生"与"编造"在数据上看不出区别 —— 一份多写了三条无出处条款的合同，
    与一份老实的合同长得一模一样。缺来源说明**派生逻辑漏登记了**（或有人往 payload 里
    加了新字段），那是必须当场修的事，不能让它在库里静静躺着。
    """
    covered = {s["field_path"] for s in sources}
    missing = sorted(_enumerate_field_paths(payload) - covered)
    if missing:
        raise ContractError(
            "合同派生中止：以下字段没有登记来源，无法核对（每个写入字段都必须有来源行）："
            + "、".join(missing)
        )
    dead = sorted(covered - _enumerate_field_paths(payload))
    if dead:
        # 反向也要查：来源指向一个合同里并不存在的字段，说明来源与内容已经脱节，
        # 留在库里会让"逐字段可核对"变成一句空话（核对的人找不到那个字段）。
        raise ContractError(
            "合同派生中止：字段来源表登记了合同里不存在的字段路径：" + "、".join(dead)
        )
    unknown = sorted({s["source_kind"] for s in sources} - ALL_SOURCE_KINDS)
    if unknown:
        raise ContractError("合同派生中止：来源类别不在取值域内：" + "、".join(unknown))


# ── 命令：派生 ──────────────────────────────────────────────────────────────


def derive_contract(
    session: Session, *, release_id: int, actor_user_id: int, note: str | None = None
) -> dict[str, Any]:
    """从**已接受的那一条发布**派生一份合同核对稿（幂等由调用方的 `run_write` 负责）。

    Raises:
        ContractNotFoundError: 发布记录不存在（404）。
        ContractStateError: 未响应 / 被拒绝 / 已派生过（409）。
        ContractError: 被接受的不是对客报价、冻结快照为空（400）。
        reg.ArtifactPayloadError: 生成的合同不符合注册表字段契约（400）。
    """
    release = offers_svc.get_release(session, release_id=release_id)
    if release is None:
        raise ContractNotFoundError("发布记录不存在")

    # ① 前置：**已接受事实**。未响应、或响应是拒绝，都不能派生。
    response = offers_svc.response_of(session, release_id=release_id)
    if response is None:
        raise ContractStateError(
            "这条发布还没有客户响应，不能生成合同 —— "
            "BP-03 第 8 条要求合同从**已接受事实**派生，尚未被响应的报价不是已接受事实"
        )
    if str(response["decision"]) != offers_svc.DECISION_ACCEPT:
        raise ContractStateError(
            f"客户对该发布的响应是 {response['decision']}，不是接受 ⇒ 不能生成合同"
        )

    # ② 前置：被接受的必须是对客报价（见 ACCEPTABLE_QUOTE_TYPE 的说明）。
    snapshot = release["snapshot"] or {}
    quote_type = str(snapshot.get("artifact_type") or "")
    if quote_type != ACCEPTABLE_QUOTE_TYPE:
        raise ContractError(
            f"这条发布的是 {quote_type or '未知类型'}，不是对客报价 ⇒ 它的接受不构成"
            f"商业承诺，不能据此生成合同（只有 {ACCEPTABLE_QUOTE_TYPE} 的接受才是"
            "BP-03 意义上的已接受事实）"
        )

    # ③ 前置：冻结快照必须有内容。快照是这次发布的**全部价值**，空了就没得派生。
    quote = snapshot.get("payload") or {}
    if not quote:
        raise ContractError(
            "这条发布冻结的客户投影是空的 ⇒ 派生出的合同会是空壳，已中止"
            "（发布记录本身有缺陷，应先查清再派生）"
        )

    # ④ 已派生过：判据是 DB 唯一约束，这里只是**先把话说清楚**（并发由 except 兜住）。
    existing = get_derivation_by_release(session, release_id=release_id)
    if existing is not None:
        raise ContractStateError(
            f"这份已接受报价已经派生过合同（合同成果 #{existing['contract_artifact_id']}，"
            "派生记录 "
            f"#{existing['derivation_id']}）—— 一份已接受事实只派生一份合同；"
            "要改内容请编辑那份合同的后续版本",
            existing_contract_artifact_id=int(existing["contract_artifact_id"]),
        )

    assignment = load_assignment(session, int(release["assignment_id"]))
    if assignment is None:
        raise ContractNotFoundError("发布记录不存在")

    payload, sources, meta = _build(
        session, release=release, response=response, assignment=assignment
    )
    _assert_every_field_has_source(payload, sources)
    reg.validate_payload(CONTRACT_TYPE, payload)  # 契约校验不因"自己写库"而省略

    now = utcnow_naive()
    stamp = _fmt(now)
    assignment_id = int(assignment["id"])
    note_final = note or (
        f"由客户接受的对客报价派生（{TEMPLATE_REF}）"
        + (
            f"；冻结快照中未提供：{'、'.join(meta['absent_quote_fields'])}"
            if meta["absent_quote_fields"]
            else ""
        )
    )

    try:
        artifact_row = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_artifact "
                    "(entrustment_id, assignment_id, artifact_type, current_revision_id, "
                    " status, created_at, updated_at) "
                    "VALUES (:eid, :aid, :atype, NULL, :status, :ts, :ts)"
                ),
                {
                    "eid": release["entrustment_id"],
                    "aid": assignment_id,
                    "atype": CONTRACT_TYPE,
                    "status": art.STATUS_ACTIVE,
                    "ts": stamp,
                },
            ),
        )
        contract_artifact_id = int(artifact_row.lastrowid or 0)

        revision_row = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_artifact_revision "
                    "(artifact_id, revision_no, payload_json, source, note, created_by, created_at) "
                    "VALUES (:aid, 1, :payload, :source, :note, :by, :ts)"
                ),
                {
                    "aid": contract_artifact_id,
                    "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    # `manual` 而不是 `agent`：这是**确定性派生**，不是模型产出。
                    # 标成 agent 会让它掉进 source_gate 的"模型产出却无声明 ⇒ 拒发"分支，
                    # 而这里的可核对性由字段来源表承担（见模块文档）。
                    "source": art.SOURCE_MANUAL,
                    "note": note_final[:500],
                    "by": actor_user_id,
                    "ts": stamp,
                },
            ),
        )
        contract_revision_id = int(revision_row.lastrowid or 0)
        session.execute(
            text("UPDATE ent_artifact SET current_revision_id = :rid WHERE id = :aid"),
            {"rid": contract_revision_id, "aid": contract_artifact_id},
        )

        derivation_row = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_contract_derivation "
                    "(assignment_id, entrustment_id, release_id, response_id, quote_artifact_id, "
                    " quote_revision_id, quote_revision_no, contract_artifact_id, "
                    " contract_revision_id, contract_revision_no, template_code, template_version, "
                    " effective_date, derived_by, derived_at, note, created_at, updated_at) "
                    "VALUES (:aid, :eid, :rid, :resp, :qart, :qrevid, :qrev, :cart, :crevid, "
                    " :crev, :tcode, :tver, :eff, :by, :now, :note, :now, :now)"
                ),
                {
                    "aid": assignment_id,
                    "eid": release["entrustment_id"],
                    "rid": release_id,
                    "resp": int(response["response_id"]),
                    "qart": int(release["artifact_id"]),
                    "qrevid": int(release["revision_id"]),
                    "qrev": int(release["revision_no"]),
                    "cart": contract_artifact_id,
                    "crevid": contract_revision_id,
                    "crev": 1,
                    "tcode": TEMPLATE_CODE,
                    "tver": TEMPLATE_VERSION,
                    "eff": payload.get("effective_date"),
                    "by": actor_user_id,
                    "now": stamp,
                    "note": note_final[:500],
                },
            ),
        )
        derivation_id = int(derivation_row.lastrowid or 0)

        for item in sources:
            session.execute(
                text(
                    "INSERT INTO ent_contract_field_source "
                    "(derivation_id, field_path, value_text, source_kind, source_ref, created_at) "
                    "VALUES (:did, :path, :value, :kind, :ref, :ts)"
                ),
                {
                    "did": derivation_id,
                    "path": item["field_path"],
                    "value": item["value_text"],
                    "kind": item["source_kind"],
                    "ref": item["source_ref"],
                    "ts": stamp,
                },
            )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # 并发或重放：唯一约束是判据。**不吞**成成功，也**不让它变成 500**。
        found = get_derivation_by_release(session, release_id=release_id)
        raise ContractStateError(
            "这份已接受报价已被派生过合同（并发/重复请求被唯一约束挡住）—— "
            "一份已接受事实只派生一份合同",
            existing_contract_artifact_id=(
                int(found["contract_artifact_id"]) if found is not None else None
            ),
        ) from exc

    created = get_derivation_by_release(session, release_id=release_id)
    if created is None:
        raise ContractError("派生记录写入后读不到（不该发生）")
    # 写读一致性自检：合同成果必须真的存在，否则会留下"有派生记录、没有合同"的悬空态。
    contract = art.get_artifact(session, contract_artifact_id)
    if str(contract["artifact_type"]) != CONTRACT_TYPE:
        raise ContractError(
            "派生出的成果类型不是合同核对稿（写读键名不一致）—— 已中止，不留悬空派生记录"
        )
    created["absent_quote_fields"] = meta["absent_quote_fields"]
    created["field_source_count"] = len(sources)
    return created


# ── 投影 ────────────────────────────────────────────────────────────────────


def project_derivation(session: Session, derivation: dict[str, Any]) -> dict[str, Any]:
    """经理视角：派生关系 + **逐字段来源表** + 如实列出的缺失项。

    刻意**不**做客户投影：字段来源表里带 `release:12@v3` / `leg:4` 这类内部编号，
    那是审计信息。客户要看合同走**已有的发布通路**（把这份 `contract_review` 发布出去、
    客户在 `/my-offer-releases` 读冻结快照）—— 不为"客户看合同"新开一条通道，
    否则"客户能看到什么"会有两个判据（与 §6.3 的白名单下载同一条纪律）。
    """
    return {
        "derivation_id": int(derivation["derivation_id"]),
        "assignment_id": int(derivation["assignment_id"]),
        "entrustment_id": derivation["entrustment_id"],
        "release_id": int(derivation["release_id"]),
        "response_id": int(derivation["response_id"]),
        # 派生自**哪一版**：报价与合同各自的精确版本都摆出来（D1-08 的 inspection 面）
        "quote_artifact_id": int(derivation["quote_artifact_id"]),
        "quote_revision_id": int(derivation["quote_revision_id"]),
        "quote_revision_no": int(derivation["quote_revision_no"]),
        "contract_artifact_id": int(derivation["contract_artifact_id"]),
        "contract_revision_id": int(derivation["contract_revision_id"]),
        "contract_revision_no": int(derivation["contract_revision_no"]),
        "template_code": str(derivation["template_code"]),
        "template_version": str(derivation["template_version"]),
        "effective_date": derivation["effective_date"],
        "derived_by": int(derivation["derived_by"]),
        "derived_at": str(derivation["derived_at"]),
        "note": derivation["note"],
        "absent_quote_fields": list(derivation.get("absent_quote_fields") or []),
        "field_sources": list_field_sources(
            session, derivation_id=int(derivation["derivation_id"])
        ),
    }


# ── 签署证据（§10.1 第 7 步的后半 / D1-08 的 `linked evidence`）──────────────
#
# 前半"生成合同"已落；这里补的是"**记录**签署证据并绑到合同的某个版本"。
# 没有这一块，D1-08 的判据 `Contract version and linked evidence inspection`
# 只有前半句成立 —— "合同版本"查得到，"关联证据"无处可查。
#
# ⚠️ 一条必须写死的边界：**本模块不制造签署**。它只记录"有这样一份标注为
# 样件的证据"这件事。合同 §3.2 / D1-08 要求证据与状态不得等同于实时电子签，
# 所以 `mode` 恒为 `labeled_sample`、且它是**服务端写死**的常量而不是入参 ——
# 让调用方能传 `mode=live` 就等于让界面能自称"已完成电子签署"。

#: 签署证据模式。与 `offers.SIGNATURE_MODE_LABELED_SAMPLE` 是**同一个常量**：
#: 两边各写一份字符串，改一处另一处静默留下旧口径（航段 mode 标签表那次已踩过）。
SIGNATURE_MODE = offers_svc.SIGNATURE_MODE_LABELED_SAMPLE

KIND_SAMPLE_SCAN = "sample_scan"
KIND_WRITTEN_CONFIRMATION = "written_confirmation"
KIND_MANUAL_RECORD = "manual_record"
ALL_EVIDENCE_KINDS = frozenset({KIND_SAMPLE_SCAN, KIND_WRITTEN_CONFIRMATION, KIND_MANUAL_RECORD})

#: 形态标签（**唯一**实现）。⛔ 前端不得自己再存一份 —— 两处各存一份 ⇒
#: 改一处、另一处静默显示旧说法（与 `plan.MODE_LABELS` 同一条纪律）。
EVIDENCE_KIND_LABELS = {
    KIND_SAMPLE_SCAN: "样件扫描件",
    KIND_WRITTEN_CONFIRMATION: "书面确认",
    KIND_MANUAL_RECORD: "人工记录",
}

#: 形态的**展示顺序**（`frozenset` 无序，界面上的选择条需要一个稳定顺序）。
EVIDENCE_KIND_ORDER = (KIND_SAMPLE_SCAN, KIND_WRITTEN_CONFIRMATION, KIND_MANUAL_RECORD)

#: 模式标签。这句话是**合同要求常驻**的声明，不是装饰文案 ——
#: 删掉它就等于允许"一份证据"被读成"签过了"。
SIGNATURE_MODE_LABELS = {
    SIGNATURE_MODE: "样件标注（不构成实时电子签署）",
}


class SignatureEvidenceError(RuntimeError):
    """签署证据的基础异常。HTTP 层按语义转 4xx（默认 400）。"""


class SignatureEvidenceNotFoundError(SignatureEvidenceError):
    """合同成果 / 指定版本不存在。HTTP 层应转 404。"""


class SignatureEvidenceStateError(SignatureEvidenceError):
    """同一版合同同一形态已经记过一条。HTTP 层应转 409。

    `existing_evidence_id`：带上已存在的那条 —— "已经记过了"是一句没用的拒绝，
    客户端需要能直接去读它（与 `ContractStateError` 同一取向）。
    """

    def __init__(self, message: str, *, existing_evidence_id: int | None = None) -> None:
        super().__init__(message)
        self.existing_evidence_id = existing_evidence_id


_SIG_COLS = (
    "id, assignment_id, entrustment_id, contract_artifact_id, contract_revision_id, "
    "contract_revision_no, mode, evidence_kind, note, recorded_by, recorded_at"
)


def _row_to_signature(row: Any) -> dict[str, Any]:
    return {
        "evidence_id": int(row["id"]),
        "assignment_id": int(row["assignment_id"]),
        "entrustment_id": (
            int(row["entrustment_id"]) if row["entrustment_id"] is not None else None
        ),
        "contract_artifact_id": int(row["contract_artifact_id"]),
        "contract_revision_id": int(row["contract_revision_id"]),
        "contract_revision_no": int(row["contract_revision_no"]),
        "mode": str(row["mode"]),
        "evidence_kind": str(row["evidence_kind"]),
        "note": row["note"],
        "recorded_by": int(row["recorded_by"]) if row["recorded_by"] is not None else None,
        "recorded_at": _fmt(row["recorded_at"]),
    }


def list_signature_evidence(session: Session, *, contract_artifact_id: int) -> list[dict[str, Any]]:
    """该合同**全部版本**的签署证据（按版本升序、同版本按形态）。"""
    rows = session.execute(
        text(
            f"SELECT {_SIG_COLS} FROM ent_contract_signature_evidence "
            "WHERE contract_artifact_id = :aid ORDER BY contract_revision_no, evidence_kind"
        ),
        {"aid": contract_artifact_id},
    ).mappings()
    return [_row_to_signature(r) for r in rows]


def record_signature_evidence(
    session: Session,
    *,
    contract_artifact_id: int,
    evidence_kind: str,
    actor_user_id: int,
    note: str | None = None,
    revision_no: int | None = None,
) -> dict[str, Any]:
    """就合同的**某个版本**记一条签署证据（幂等由调用方的 `run_write` 负责）。

    `revision_no` 省略时取合同的**当前版本** —— 界面上的主路径就是"给现在这版记证据"。

    Raises:
        art.ArtifactNotFoundError: 合同成果不存在（404）。
        SignatureEvidenceNotFoundError: 指定版本不存在（404）。
        SignatureEvidenceError: 成果不是合同核对稿、形态不在取值域（400）。
        SignatureEvidenceStateError: 该版本该形态已记过（409）。
    """
    contract = art.get_artifact(session, contract_artifact_id)
    if str(contract["artifact_type"]) != CONTRACT_TYPE:
        raise SignatureEvidenceError(
            f"成果 #{contract_artifact_id} 的类型是 {contract['artifact_type']}，"
            f"不是 {CONTRACT_TYPE} ⇒ 不能记合同签署证据"
        )

    kind = str(evidence_kind or "").strip()
    if kind not in ALL_EVIDENCE_KINDS:
        raise SignatureEvidenceError(
            f"证据形态 {kind or '（空）'} 不在取值域内（{'、'.join(sorted(ALL_EVIDENCE_KINDS))}）—— "
            "未知形态进库之后，「这份证据到底存不存在实物」这个问题就再也没有答案"
        )

    # 版本定位：**显式版本优先**，省略取当前版本。⚠️ 不给"猜一个版本"的分支：
    # 证据绑错版本之后，"客户签的是哪一版"在库里就是错的，而这正是本切片的全部价值。
    want_no = int(revision_no) if revision_no is not None else None
    if want_no is None:
        current = contract.get("current_revision") or {}
        want_no = int(current["revision_no"]) if current.get("revision_no") is not None else 1
    rev = (
        session.execute(
            text(
                "SELECT id, revision_no FROM ent_artifact_revision "
                "WHERE artifact_id = :aid AND revision_no = :no"
            ),
            {"aid": contract_artifact_id, "no": want_no},
        )
        .mappings()
        .first()
    )
    if rev is None:
        raise SignatureEvidenceNotFoundError(
            f"合同 #{contract_artifact_id} 没有第 {want_no} 版 ⇒ 没有可绑证据的版本"
        )

    assignment_id = contract["assignment_id"]
    if assignment_id is None:
        # 合同成果没有委托单 ⇒ 证据无处可挂。宁可在这里炸，也不要挂到一个"不知道属于哪单"的行上。
        raise SignatureEvidenceError(
            f"合同 #{contract_artifact_id} 没有关联委托单 ⇒ 签署证据没有可归属的单据，已中止"
        )

    stamp = _fmt(utcnow_naive())
    try:
        row = cast(
            "CursorResult[Any]",
            session.execute(
                text(
                    "INSERT INTO ent_contract_signature_evidence "
                    "(assignment_id, entrustment_id, contract_artifact_id, contract_revision_id, "
                    " contract_revision_no, mode, evidence_kind, note, recorded_by, recorded_at) "
                    "VALUES (:aid, :eid, :cart, :crev, :cno, :mode, :kind, :note, :by, :ts)"
                ),
                {
                    "aid": int(assignment_id),
                    "eid": contract["entrustment_id"],
                    "cart": contract_artifact_id,
                    "crev": int(rev["id"]),
                    "cno": int(rev["revision_no"]),
                    # ⛔ 不是入参：模式由服务端写死（见本节开头的边界说明）
                    "mode": SIGNATURE_MODE,
                    "kind": kind,
                    "note": (note or None),
                    "by": actor_user_id,
                    "ts": stamp,
                },
            ),
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = (
            session.execute(
                text(
                    "SELECT id FROM ent_contract_signature_evidence "
                    "WHERE contract_revision_id = :rid AND evidence_kind = :kind"
                ),
                {"rid": int(rev["id"]), "kind": kind},
            )
            .mappings()
            .first()
        )
        raise SignatureEvidenceStateError(
            f"合同第 {int(rev['revision_no'])} 版已经记过一条"
            f"「{EVIDENCE_KIND_LABELS.get(kind, kind)}」证据 —— "
            "同一版同一形态只记一条（这是重放/双击被唯一约束挡住，不是业务冲突）",
            existing_evidence_id=int(existing["id"]) if existing is not None else None,
        ) from exc

    evidence_id = int(row.lastrowid or 0)
    got = (
        session.execute(
            text(f"SELECT {_SIG_COLS} FROM ent_contract_signature_evidence WHERE id = :eid"),
            {"eid": evidence_id},
        )
        .mappings()
        .first()
    )
    if got is None:
        raise SignatureEvidenceError("证据写入后读不到（不该发生）")
    return project_signature_evidence(_row_to_signature(got))


def project_signature_evidence(item: dict[str, Any]) -> dict[str, Any]:
    """一条签署证据的**经理投影**：原值与显示文案**同时给出**。

    同时给的理由与航段一致：界面既要能显示"样件扫描件"，也要能在回显/比对时
    拿到 `sample_scan` 这个原值 —— 只给文案的话，"这两个取值其实不同"这件事
    在库里就再也看不出来。
    """
    kind = str(item["evidence_kind"])
    mode = str(item["mode"])
    note = str(item["note"] or "").strip()
    return {
        "evidence_id": int(item["evidence_id"]),
        "assignment_id": int(item["assignment_id"]),
        "entrustment_id": item["entrustment_id"],
        "contract_artifact_id": int(item["contract_artifact_id"]),
        "contract_revision_id": int(item["contract_revision_id"]),
        "contract_revision_no": int(item["contract_revision_no"]),
        "revision_no_text": f"第 {int(item['contract_revision_no'])} 版",
        "mode": mode,
        "mode_text": SIGNATURE_MODE_LABELS.get(mode, mode),
        "evidence_kind": kind,
        "evidence_kind_text": EVIDENCE_KIND_LABELS.get(kind, kind),
        "note": note,
        "note_text": note or "未写说明",
        "recorded_by": item["recorded_by"],
        "recorded_at": str(item["recorded_at"]),
    }


def project_signature_evidence_list(
    session: Session, *, contract_artifact_id: int
) -> dict[str, Any]:
    """某份合同的签署证据清单（**空列表也要能区分于读不到** ⇒ 带上 artifact_id）。"""
    items = list_signature_evidence(session, contract_artifact_id=contract_artifact_id)
    return {
        "contract_artifact_id": int(contract_artifact_id),
        "items": [project_signature_evidence(it) for it in items],
        "has_items": bool(items),
        # 形态选项**由服务端给出**（标签唯一实现在 `EVIDENCE_KIND_LABELS`）：
        # 前端自己存一份 ⇒ 改一处、另一处静默显示旧说法；而"这个形态到底能不能选"
        # 本就是服务端的取值域问题（`ALL_EVIDENCE_KINDS`）。
        "kind_options": [
            {"value": k, "label": EVIDENCE_KIND_LABELS[k]} for k in EVIDENCE_KIND_ORDER
        ],
        # 常驻声明：它是**合同要求**的措辞，不是界面装饰 —— 这一段在前端也要原样显示。
        "disclaimer": (
            f"签署证据按{SIGNATURE_MODE_LABELS.get(SIGNATURE_MODE, SIGNATURE_MODE)}"
            "记录，不构成实时电子签署。"
        ),
    }


__all__ = [
    "ACCEPTABLE_QUOTE_TYPE",
    "ALL_EVIDENCE_KINDS",
    "ALL_SOURCE_KINDS",
    "CONTRACT_TYPE",
    "ContractError",
    "ContractNotFoundError",
    "ContractStateError",
    "EVIDENCE_KIND_LABELS",
    "EVIDENCE_KIND_ORDER",
    "KIND_MANUAL_RECORD",
    "KIND_SAMPLE_SCAN",
    "KIND_WRITTEN_CONFIRMATION",
    "SIGNATURE_MODE",
    "SIGNATURE_MODE_LABELS",
    "SignatureEvidenceError",
    "SignatureEvidenceNotFoundError",
    "SignatureEvidenceStateError",
    "SOURCE_ACCEPTED_RELEASE",
    "SOURCE_ASSIGNMENT",
    "SOURCE_KIND_LABELS",
    "SOURCE_CUSTOMER_RESPONSE",
    "SOURCE_LEG",
    "SOURCE_ORGANIZATION",
    "SOURCE_TEMPLATE",
    "TEMPLATE_CODE",
    "TEMPLATE_REF",
    "TEMPLATE_VERSION",
    "derive_contract",
    "get_derivation_by_contract",
    "get_derivation_by_release",
    "list_field_sources",
    "list_signature_evidence",
    "project_derivation",
    "project_signature_evidence",
    "project_signature_evidence_list",
    "record_signature_evidence",
    "utcnow_naive",
]
