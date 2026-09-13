"""成果类型注册表 —— 成果的**取值域与字段契约**（S1 第 5 条）。

为什么需要一张注册表
--------------------
ENT-004/006 落地了"成果有版本"的机制，但 `artifact_type` 当时是**任意字符串**：
调用方写 `quote_parsed` 还是 `quote-parsed` 都能落库，字段也可以随便塞。
机制（版本）与语义（这是什么成果、有哪些字段、哪些字段不能给客户看）
是两件事，注册表补的是后者：

1. **取值域**：未知类型一律拒绝（400），不静默接受；
2. **字段契约**：每种类型声明自己的字段清单，创建/追加版本时校验
   "必填字段存在"，缺项**如实保留为缺项**（写进 `missing_fields`，不编造默认值）；
3. **投影依据**：`internal_fields` 标出内部成本类字段，作为"客户数据白名单投影"
   的声明式依据 —— 哪些字段绝不可出现在客户侧，写在注册表里而不是散落在接口里
   （R1 的投影严格分叉在 S3 的 `customer_offer` / 结算层落地）；
4. **证据绑定**：`evidence_kinds` 声明该类型可挂哪些证据类别（附件/合同/收付…），
   附件绑定与证据校验共用同一份取值域（`attachments.py`）。

字段命名约定
------------
字段名用 `snake_case` 英文标识符，不写中文键 —— 中文是**展示**层的事（`label`），
把展示文案混进数据结构会让字段对比清单与客户投影都变得脆弱。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── 证据类别的单一取值域（与 tasks.EVIDENCE_KINDS 同义，此处作为成果侧入口） ──

EVIDENCE_DOCUMENT = "document"
EVIDENCE_PHOTO = "photo"
EVIDENCE_EMAIL = "email"
EVIDENCE_RECEIPT = "receipt"
EVIDENCE_CONTRACT = "contract"
EVIDENCE_PAYMENT = "payment"
EVIDENCE_CONFIRMATION = "confirmation"

ALL_EVIDENCE_KINDS = frozenset(
    {
        EVIDENCE_DOCUMENT,
        EVIDENCE_PHOTO,
        EVIDENCE_EMAIL,
        EVIDENCE_RECEIPT,
        EVIDENCE_CONTRACT,
        EVIDENCE_PAYMENT,
        EVIDENCE_CONFIRMATION,
    }
)


@dataclass(frozen=True)
class ArtifactTypeSpec:
    """一种成果类型的契约。"""

    code: str
    label: str
    #: 创建/追加版本时必须出现的字段（缺失不报错，但会进入 missing_fields）
    required_fields: tuple[str, ...] = ()
    #: 允许出现但不强制的字段；不在 required + optional 内的字段被视为未知字段
    optional_fields: tuple[str, ...] = ()
    #: **内部字段**：绝不可进入客户投影（内部采购价、毛利、供应商比价、内部会话）
    internal_fields: tuple[str, ...] = ()
    #: 该类型可绑定的证据类别
    evidence_kinds: tuple[str, ...] = ()
    #: 是否允许人工直接编辑（R1 全为 True；保留位体现"只读计算类成果"的差异）
    editable: bool = True

    @property
    def known_fields(self) -> frozenset[str]:
        return frozenset(self.required_fields) | frozenset(self.optional_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "internal_fields": list(self.internal_fields),
            "evidence_kinds": list(self.evidence_kinds),
            "editable": self.editable,
        }


# ── 注册表本体（R1 固定取值域；新增类型必须同时补字段契约与测试） ──────────────

_SPECS: tuple[ArtifactTypeSpec, ...] = (
    ArtifactTypeSpec(
        code="quote_parsed",
        label="报价解析稿",
        required_fields=("carrier", "rate"),
        optional_fields=("cargo_name", "quantity", "quantity_unit", "route", "valid_until"),
        internal_fields=(),
        evidence_kinds=(EVIDENCE_DOCUMENT, EVIDENCE_EMAIL, EVIDENCE_PHOTO),
    ),
    ArtifactTypeSpec(
        code="supplier_compare",
        label="供应报价对比",
        required_fields=("candidates",),
        optional_fields=("selected_candidate", "comparison_note"),
        # 供应商比价与成本口径属于内部信息（计划 §2.1「客户端字段白名单」）
        internal_fields=("candidates", "selected_candidate", "comparison_note"),
        evidence_kinds=(EVIDENCE_DOCUMENT, EVIDENCE_EMAIL),
    ),
    ArtifactTypeSpec(
        code="customer_quote",
        label="对客方案与报价（定版后为客户可见版本）",
        required_fields=("amount", "currency", "includes"),
        optional_fields=("valid_until", "excludes", "note"),
        # 对客报价本身要能到客户手里，但内部成本口径绝不在其中
        internal_fields=(),
        evidence_kinds=(EVIDENCE_DOCUMENT, EVIDENCE_CONFIRMATION),
    ),
    ArtifactTypeSpec(
        code="contract_review",
        label="合同核对稿",
        required_fields=("parties",),
        optional_fields=("clauses", "effective_date", "note"),
        internal_fields=(),
        evidence_kinds=(EVIDENCE_CONTRACT, EVIDENCE_DOCUMENT),
    ),
    ArtifactTypeSpec(
        code="procurement_confirm",
        label="采购确认",
        required_fields=("supplier", "agreed_scope"),
        optional_fields=("agreed_amount", "currency", "effective_from"),
        # 采购价与毛利是内部事实，客户侧只能看到对客报价
        internal_fields=("agreed_amount", "currency", "supplier"),
        evidence_kinds=(EVIDENCE_RECEIPT, EVIDENCE_PAYMENT, EVIDENCE_DOCUMENT),
    ),
    ArtifactTypeSpec(
        code="settlement_draft",
        label="结算草稿",
        required_fields=("receivable_lines",),
        optional_fields=("payable_lines", "disputed", "note"),
        internal_fields=("payable_lines",),
        evidence_kinds=(EVIDENCE_RECEIPT, EVIDENCE_PAYMENT),
    ),
)

ARTIFACT_TYPES: dict[str, ArtifactTypeSpec] = {spec.code: spec for spec in _SPECS}

#: 客户可见（可进入客户投影）的成果类型 —— 其余类型一律不可对客户开放
CUSTOMER_VISIBLE_TYPES: frozenset[str] = frozenset({"customer_quote", "contract_review"})


class UnknownArtifactTypeError(ValueError):
    """未知成果类型（取值域由注册表固定）。"""


def get_spec(artifact_type: str) -> ArtifactTypeSpec:
    """按代码取类型契约；未知类型抛错（不静默接受、不给默认）。"""
    spec = ARTIFACT_TYPES.get(artifact_type)
    if spec is None:
        raise UnknownArtifactTypeError(
            f"未知成果类型 {artifact_type!r}；R1 取值域：{sorted(ARTIFACT_TYPES)}"
        )
    return spec


def list_specs() -> list[dict[str, Any]]:
    """列出全部类型契约（供前端渲染表单与字段标签）。"""
    return [spec.to_dict() for spec in _SPECS]


def validate_payload(artifact_type: str, payload: dict[str, Any]) -> list[str]:
    """校验成果内容并返回**缺项清单**。

    两级严格度，刻意不同：

    * **类型**是取值域 —— 未知类型直接拒绝（`UnknownArtifactTypeError`），
      因为类型决定字段契约、投影规则与证据类别，含糊的类型等于没有契约；
    * **字段**不做硬拒绝 —— 未声明字段只是被记进 `unknown_fields`，
      并且**天然进不了客户投影**（白名单投影只取声明过的字段）。

    为什么不硬拒未知字段：成果内容很多是 Agent 产出（AG-02 解析船东报价），
    模型多输出一个键就把整条业务卡死，是把"契约校验"和"业务可用性"混为一谈；
    真正需要防的是**未声明的字段泄给客户**，那由白名单投影兜住。

    缺必填字段也**不报错**，而是返回缺项 —— 草稿允许不完整（计划 §2.1 第 1 步），
    但"不完整"必须被如实记录，不能靠补默认值把缺项抹掉。
    """
    spec = get_spec(artifact_type)
    return [name for name in spec.required_fields if payload.get(name) in (None, "", [], {})]


def unknown_fields(artifact_type: str, payload: dict[str, Any]) -> list[str]:
    """未在注册表中声明的字段名（记录用；不阻断写入、不进入客户投影）。"""
    spec = get_spec(artifact_type)
    return sorted(k for k in payload if k not in spec.known_fields)


def diff_payloads(
    artifact_type: str,
    from_payload: dict[str, Any],
    to_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """两个版本的**字段变化清单**（计划 §3.3「成果」组的「字段变化清单」）。

    只比较注册表声明的已知字段，并区分 `added` / `removed` / `changed`
    三种变化。返回按字段名排序的列表，便于前端稳定渲染与快照测试。
    """
    spec = get_spec(artifact_type)
    changes: list[dict[str, Any]] = []
    for name in sorted(spec.known_fields):
        before = from_payload.get(name)
        after = to_payload.get(name)
        if before == after:
            continue
        if before in (None, "", [], {}) and after not in (None, "", [], {}):
            kind = "added"
        elif after in (None, "", [], {}) and before not in (None, "", [], {}):
            kind = "removed"
        else:
            kind = "changed"
        changes.append(
            {
                "field": name,
                "kind": kind,
                "from": before,
                "to": after,
                "internal": name in spec.internal_fields,
            }
        )
    return changes


def project_for_customer(artifact_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """客户数据白名单投影：**先剔除、再返回**（不是先返回再让前端隐藏）。

    内部成本类字段（采购价、毛利、供应商比价、内部会话）绝不进入返回值。
    非客户可见类型一律投影为空对象 —— 宁可让客户看不到，也不泄漏内部成果。
    """
    if artifact_type not in CUSTOMER_VISIBLE_TYPES:
        return {}
    spec = get_spec(artifact_type)
    return {
        k: v for k, v in payload.items() if k in spec.known_fields and k not in spec.internal_fields
    }


__all__ = [
    "ALL_EVIDENCE_KINDS",
    "ARTIFACT_TYPES",
    "CUSTOMER_VISIBLE_TYPES",
    "ArtifactTypeSpec",
    "UnknownArtifactTypeError",
    "diff_payloads",
    "get_spec",
    "list_specs",
    "project_for_customer",
    "unknown_fields",
    "validate_payload",
]
