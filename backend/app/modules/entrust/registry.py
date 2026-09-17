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

# ── 字段**类型**的取值域（契约的一部分，不由运行时值推断）──────────────────
#
# 为什么需要它：前端原先只能**按运行时值的类型**推断编辑形态（`_fieldKind`），
# 于是**缺值**字段一律退化成单行输入框 —— `settlement_draft.receivable_lines`
# 本该是列表，在刚创建、还没填的时候会显示成文本框；用户填 `[1,2]` 存回去，
# 得到的是一段**字符串** `"[1,2]"`，缺项判定与客户投影都会跟着错。
# 类型是契约，不该由"这个值恰好长什么样"推出来。

FIELD_TEXT = "text"
FIELD_NUMBER = "number"
FIELD_LIST = "list"
FIELD_OBJECT = "object"

ALL_FIELD_KINDS = frozenset({FIELD_TEXT, FIELD_NUMBER, FIELD_LIST, FIELD_OBJECT})

#: 需要**结构化编辑器**（JSON 多行）的字段类型
STRUCTURED_FIELD_KINDS = frozenset({FIELD_LIST, FIELD_OBJECT})


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
    #: **字段类型声明**（`(字段名, 类型)` 有序元组，顺序即展示顺序）。
    #:
    #: 用元组而不是 `dict`：冻结 dataclass 的字段必须不可变，且**顺序有意义**
    #: （展示顺序不由 payload 的键序决定，见 `decorateArtifact` 的注释）。
    #: 覆盖范围**必须是全部已知字段**（required + optional），由模块底部的
    #: `_assert_registry_complete()` 在 import 期强制 —— 少一个就起不来。
    field_types: tuple[tuple[str, str], ...] = ()

    @property
    def known_fields(self) -> frozenset[str]:
        return frozenset(self.required_fields) | frozenset(self.optional_fields)

    @property
    def field_kind_map(self) -> dict[str, str]:
        """字段名 → 声明类型（供前端查表；未声明的字段不在表内）。"""
        return dict(self.field_types)

    def field_kind(self, name: str) -> str:
        """某字段的**声明类型**；未声明返回**空串**（不猜、不给默认）。

        返回空串而不是 `text`：未知字段（历史数据 / 前端未跟上的新增字段）必须
        能被识别为「没有契约」，前端据此回退到"按值推断"，而不是被伪装成文本。
        """
        for field_name, kind in self.field_types:
            if field_name == name:
                return kind
        return ""

    def undeclared_fields(self) -> list[str]:
        """已知字段里**没有类型声明**的（注册表自检用）。"""
        declared = {name for name, _ in self.field_types}
        return sorted(self.known_fields - declared)

    def mistyped_fields(self) -> list[tuple[str, str]]:
        """声明了类型但**不属于已知字段**的（拼错字段名 / 残留字段）。"""
        known = self.known_fields
        return [(name, kind) for name, kind in self.field_types if name not in known]

    def bad_field_kinds(self) -> list[tuple[str, str]]:
        """类型值**不在取值域内**的（拼错类型名 ⇒ 前端会当作"未声明"）。"""
        return [(name, kind) for name, kind in self.field_types if kind not in ALL_FIELD_KINDS]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "internal_fields": list(self.internal_fields),
            "evidence_kinds": list(self.evidence_kinds),
            "editable": self.editable,
            # 前端按此决定编辑形态：结构化类型走 JSON 多行编辑器。
            # 输出成对象（而不是有序对的数组）是因为前端只做**查表**，
            # 展示顺序由 required_fields / optional_fields 决定。
            "field_types": dict(self.field_types),
        }


# ── 注册表本体（R1 固定取值域；新增类型必须同时补字段契约与测试） ──────────────

_SPECS: tuple[ArtifactTypeSpec, ...] = (
    ArtifactTypeSpec(
        code="quote_parsed",
        label="报价解析稿",
        required_fields=("carrier", "rate"),
        optional_fields=(
            "cargo_name",
            "quantity",
            "quantity_unit",
            "route",
            "valid_until",
            # 2026-09-17（HO 0917-3 裁定二）：下面四项此前不在契约里，于是真实模型
            # 给出的 `currency` / `includes` / `excludes` 被登记成**未知字段** ——
            # 而它们恰好是 BP-02 要展示的业务内容，不能长期靠"任意 JSON 字段"承载。
            # 另：`rate` 只说"45.00"，**不说这一价是每吨还是每柜**，光有金额无法确定
            # 费用 ⇒ 计价单位必须显式落在 `rate_unit` 上。
            "currency",
            "rate_unit",
            "includes",
            "excludes",
        ),
        internal_fields=(),
        evidence_kinds=(EVIDENCE_DOCUMENT, EVIDENCE_EMAIL, EVIDENCE_PHOTO),
        field_types=(
            ("carrier", FIELD_TEXT),
            ("rate", FIELD_NUMBER),
            ("cargo_name", FIELD_TEXT),
            ("quantity", FIELD_NUMBER),
            ("quantity_unit", FIELD_TEXT),
            ("route", FIELD_TEXT),
            ("valid_until", FIELD_TEXT),
            ("currency", FIELD_TEXT),
            ("rate_unit", FIELD_TEXT),
            ("includes", FIELD_LIST),
            ("excludes", FIELD_LIST),
        ),
    ),
    ArtifactTypeSpec(
        code="supplier_compare",
        label="供应报价对比",
        required_fields=("candidates",),
        optional_fields=("selected_candidate", "comparison_note"),
        # 供应商比价与成本口径属于内部信息（计划 §2.1「客户端字段白名单」）
        internal_fields=("candidates", "selected_candidate", "comparison_note"),
        evidence_kinds=(EVIDENCE_DOCUMENT, EVIDENCE_EMAIL),
        field_types=(
            ("candidates", FIELD_LIST),
            ("selected_candidate", FIELD_TEXT),
            ("comparison_note", FIELD_TEXT),
        ),
    ),
    ArtifactTypeSpec(
        code="customer_quote",
        label="对客方案与报价（定版后为客户可见版本）",
        required_fields=("amount", "currency", "includes"),
        optional_fields=("valid_until", "excludes", "note"),
        # 对客报价本身要能到客户手里，但内部成本口径绝不在其中
        internal_fields=(),
        evidence_kinds=(EVIDENCE_DOCUMENT, EVIDENCE_CONFIRMATION),
        field_types=(
            ("amount", FIELD_NUMBER),
            ("currency", FIELD_TEXT),
            ("includes", FIELD_LIST),
            ("valid_until", FIELD_TEXT),
            ("excludes", FIELD_LIST),
            ("note", FIELD_TEXT),
        ),
    ),
    ArtifactTypeSpec(
        code="contract_review",
        label="合同核对稿",
        required_fields=("parties",),
        optional_fields=("clauses", "effective_date", "note"),
        internal_fields=(),
        evidence_kinds=(EVIDENCE_CONTRACT, EVIDENCE_DOCUMENT),
        field_types=(
            ("parties", FIELD_LIST),
            ("clauses", FIELD_LIST),
            ("effective_date", FIELD_TEXT),
            ("note", FIELD_TEXT),
        ),
    ),
    ArtifactTypeSpec(
        code="procurement_confirm",
        label="采购确认",
        required_fields=("supplier", "agreed_scope"),
        optional_fields=("agreed_amount", "currency", "effective_from"),
        # 采购价与毛利是内部事实，客户侧只能看到对客报价
        internal_fields=("agreed_amount", "currency", "supplier"),
        evidence_kinds=(EVIDENCE_RECEIPT, EVIDENCE_PAYMENT, EVIDENCE_DOCUMENT),
        field_types=(
            ("supplier", FIELD_TEXT),
            ("agreed_scope", FIELD_TEXT),
            ("agreed_amount", FIELD_NUMBER),
            ("currency", FIELD_TEXT),
            ("effective_from", FIELD_TEXT),
        ),
    ),
    ArtifactTypeSpec(
        code="settlement_draft",
        label="结算草稿",
        required_fields=("receivable_lines",),
        optional_fields=("payable_lines", "disputed", "note"),
        internal_fields=("payable_lines",),
        evidence_kinds=(EVIDENCE_RECEIPT, EVIDENCE_PAYMENT),
        field_types=(
            ("receivable_lines", FIELD_LIST),
            ("payable_lines", FIELD_LIST),
            ("disputed", FIELD_LIST),
            ("note", FIELD_TEXT),
        ),
    ),
)

ARTIFACT_TYPES: dict[str, ArtifactTypeSpec] = {spec.code: spec for spec in _SPECS}

#: 客户可见（可进入客户投影）的成果类型 —— 其余类型一律不可对客户开放
CUSTOMER_VISIBLE_TYPES: frozenset[str] = frozenset({"customer_quote", "contract_review"})


def _assert_registry_complete() -> None:
    """注册表完整性自检 —— **在 import 期 fail fast**。

    三条都必须成立：

    1. 每个**已知字段**（required + optional）都有类型声明。少一个，前端在该字段
       缺值时就会退回"按值猜"，而缺值字段必然猜错 —— 这正是本次要修的问题；
    2. 声明的字段名必须**属于已知字段**。拼错名字不会报错、只会静默失效；
    3. 类型值必须在**取值域**内。拼错类型名会被前端当成"没有契约"。

    为什么放在 import 期而不是只写测试：`_SPECS` 是**编译期常量**，它错了就没有
    "正确的运行方式"。让进程在启动前失败，胜过让某个缺值字段在界面上静静退化成
    单行输入框 —— 后者要等到有人真的编辑那个字段才会被发现。
    """
    problems: list[str] = []
    for spec in _SPECS:
        for name in spec.undeclared_fields():
            problems.append(f"{spec.code}.{name} 缺少类型声明")
        for name, _kind in spec.mistyped_fields():
            problems.append(f"{spec.code}.{name} 声明了类型但不在已知字段内")
        for name, kind in spec.bad_field_kinds():
            problems.append(
                f"{spec.code}.{name} 的类型 {kind!r} 不在取值域 {sorted(ALL_FIELD_KINDS)}"
            )
    if problems:
        raise ValueError("成果注册表不完整：" + "；".join(problems))


_assert_registry_complete()


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
    "ALL_FIELD_KINDS",
    "ARTIFACT_TYPES",
    "CUSTOMER_VISIBLE_TYPES",
    "FIELD_LIST",
    "FIELD_NUMBER",
    "FIELD_OBJECT",
    "FIELD_TEXT",
    "STRUCTURED_FIELD_KINDS",
    "ArtifactTypeSpec",
    "UnknownArtifactTypeError",
    "diff_payloads",
    "get_spec",
    "list_specs",
    "project_for_customer",
    "unknown_fields",
    "validate_payload",
]
