"""知识库与检索引擎（F12 RAG）。

设计要点（对齐工程底线）：
- 知识库从 assistant 的单一巨型 prompt 拆成可检索的 chunk 文档
  （每港一篇 + 角色/流程/支付/货类/撮合/认证各一篇），支持精确命中；
- 检索引擎 = **字符 bigram TF-IDF 余弦相似度**：纯标准库、零外部依赖、
  结果确定性可测（CI 无网络）；
- embedding 向量检索留 ``EMBEDDING_PROVIDER`` 配置替换点（后续接
  向量库/远程 embedding API 时仅替换 ``search`` 实现，文档层不变）。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 知识文档
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeDoc:
    """一条可检索的知识文档。"""

    id: str
    topic: str
    text: str


def _port(idx: str, name: str, code: str, kind: str, desc: str) -> KnowledgeDoc:
    return KnowledgeDoc(
        id=f"port-{idx}",
        topic=f"航线港口：{name}（{code}）",
        text=f"{name}，港口代码 {code}，{kind}。{desc}",
    )


KNOWLEDGE_BASE: list[KnowledgeDoc] = [
    # ---- 航线港口（13 个，一港一篇，支持单港精确命中） ----
    _port("nng", "南宁", "NNG", "内河港", "平陆运河江海联运枢纽，运河起点，货源腹地覆盖首府经济圈。"),
    _port("ggu", "贵港", "GGU", "内河港", "广西内河第一大港，煤炭/水泥/钢材等大宗散货核心中转港。"),
    _port("wuz", "梧州", "WUZ", "内河港", "东向粤港澳大湾区门户，西江黄金水道咽喉，集装箱与件杂货优势明显。"),
    _port("bin", "来宾", "BIN", "内河港", "西江中游节点，铝工业与糖业货源为主。"),
    _port("lzh", "柳州", "LZH", "内河港", "工业重镇，钢材、汽车零部件等件杂货货源充足。"),
    _port("bsz", "百色", "BSZ", "内河港", "右江上游节点，铝土矿等资源型货源为主。"),
    _port("chz", "崇左", "CHZ", "内河港", "左江通道，面向东盟的边境货源集散地。"),
    _port("gxl", "桂林", "GXL", "内河港", "漓江上游节点，旅游与轻工货源为主。"),
    _port("hez", "贺州", "HEZ", "内河港", "桂东节点，大理石、建材货源为主。"),
    _port("yul", "玉林", "YUL", "内河港", "桂东南节点，对接北部湾的腹地货源。"),
    _port("qnz", "钦州", "QNZ", "海港", "北部湾集装箱干线港，海运与运河联运的出海关键节点。"),
    _port("fcg", "防城港", "FCG", "海港", "北部湾大宗散货主力港，铁矿、粮食等大宗中转。"),
    _port("bhz", "北海", "BHZ", "海港", "北部湾三港区之一，石化、粮油产业货源。"),
    # ---- 角色 ----
    KnowledgeDoc(
        id="roles",
        topic="平台三角色",
        text=(
            "平台有三类角色：货主（发货找船，发布货源并下单）、"
            "船东（接单找货，船舶需先提交审核，关键信息变更会自动降级重审）、"
            "港口方（泊位调度与船舶审核）。一个账号可绑定多个角色并切换。"
        ),
    ),
    # ---- 主流程 ----
    KnowledgeDoc(
        id="flow-main",
        topic="交易主流程",
        text=(
            "主流程：货主发布货源（可直接发布进撮合池）→ 智能撮合（硬约束过滤+评分排序，"
            "只推荐不出单）→ 货主选定下单 → 支付运费 → 船东启运 → 货主签收完成；"
            "撮合后任意一方可撤单（已付款自动全额退款）。"
        ),
    ),
    KnowledgeDoc(
        id="flow-publish",
        topic="发布货源方法",
        text=(
            "发货：进入「工作台-发布货源」，填写货物名称、货类、重量、起讫港、"
            "期望装货日期即可发布进入撮合池；也可用「智能填写」一句话描述，"
            "由货源解析 Agent 生成草稿回填表单，人工核对后提交。"
        ),
    ),
    KnowledgeDoc(
        id="flow-payment",
        topic="支付与退款",
        text=(
            "运费由货主在订单页支付（当前 MVP 阶段为模拟支付，后续接入微信支付）；"
            "撮合后双方均可撤单，已支付的订单撤单将自动全额退款。"
        ),
    ),
    KnowledgeDoc(
        id="flow-contract",
        topic="智能合同",
        text=(
            "订单生成后，双方可在订单页点「生成合同」：系统基于订单数据自动生成运输合同草稿"
            "并标注风险点（未支付/未锁价/日期临近/证书临期/液货等）；核心条款来自订单数据，"
            "补充条款由 AI 起草，草稿不具法律效力，签署前请人工审核。"
        ),
    ),
    # ---- 船型与货类 ----
    KnowledgeDoc(
        id="cargo-bulk",
        topic="散货 bulk",
        text="散货 bulk：水泥、矿、煤、砂石、粮等，由散货船承运，按吨计费为主。",
    ),
    KnowledgeDoc(
        id="cargo-general",
        topic="件杂货 general",
        text="件杂货 general：钢材、设备等，由件杂货船承运。",
    ),
    KnowledgeDoc(
        id="cargo-container",
        topic="集装箱 container",
        text="集装箱 container：标准箱运输，由集装箱船承运，适港航线以钦州/梧州等干线港为主。",
    ),
    KnowledgeDoc(
        id="cargo-tanker",
        topic="液货 tanker",
        text="液货 tanker：油品、化工液体，仅液货船可承运，属高风险货类，合同会自动标注风险。",
    ),
    KnowledgeDoc(
        id="cargo-other",
        topic="其他 other",
        text="其他 other：不归入上述四类的货物。",
    ),
    # ---- 撮合 ----
    KnowledgeDoc(
        id="match-rules",
        topic="撮合逻辑",
        text=(
            "智能撮合：硬约束（船舶已认证、证书覆盖装货期、载重足额、船型货类兼容）"
            "过滤后，按载重利用率、船型适配、船籍港就近、证书余量四项评分排序，"
            "满分 100。撮合只做推荐，出单由货主确认。"
        ),
    ),
    # ---- 船舶认证 ----
    KnowledgeDoc(
        id="ship-cert",
        topic="船舶认证审核",
        text=(
            "船东备案船舶后由港口方审核（船舶证书、船型、主尺度等），审核通过才能进入撮合池；"
            "关键信息变更会自动降级为待重审。"
        ),
    ),
]


# ---------------------------------------------------------------------------
# 检索引擎：字符 bigram TF-IDF 余弦（确定性，纯标准库）
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[\s，。；：、！？,.:;!?()\[\]【】]+")
_MIN_SCORE = 0.03  # 低于该相似度的文档视为不相关，不注入


def _normalize(text: str) -> str:
    """切去标点空白，统一小写。"""
    return _SPLIT_RE.sub("", text.lower())


def _bigrams(text: str) -> Counter[str]:
    """字符 bigram（单字文档退化为 unigram）。"""
    s = _normalize(text)
    if len(s) <= 1:
        return Counter({s: 1}) if s else Counter[str]()
    return Counter(s[i : i + 2] for i in range(len(s) - 1))


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


class KnowledgeIndex:
    """知识库索引：启动时一次性预计算，查询纯内存（微秒级）。

    MVP 采用词法检索（bigram TF-IDF 余弦）。``EMBEDDING_PROVIDER`` 配置
    为后续接真实向量检索的替换点：届时仅替换 ``search`` 的实现，
    ``KnowledgeDoc`` 文档层与上层 RAG 拼装逻辑均不变。
    """

    def __init__(self, docs: list[KnowledgeDoc] | None = None) -> None:
        self._docs = list(docs or KNOWLEDGE_BASE)
        self._doc_grams: list[tuple[KnowledgeDoc, Counter[str]]] = [
            (d, _bigrams(f"{d.topic} {d.text}")) for d in self._docs
        ]
        # 文档频率（含 bigram 出现的文档数）
        df: Counter[str] = Counter()
        for _, grams in self._doc_grams:
            df.update(grams.keys())
        n = max(len(self._doc_grams), 1)
        # idf 权重（平滑，避免除零）
        self._idf: dict[str, float] = {
            t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()
        }

    def _score(self, query_grams: Counter[str], doc_grams: Counter[str]) -> float:
        qa: Counter[str] = Counter(
            {t: w * self._idf.get(t, 1.0) for t, w in query_grams.items()}
        )
        da: Counter[str] = Counter(
            {t: w * self._idf.get(t, 1.0) for t, w in doc_grams.items()}
        )
        return _cosine(qa, da)

    def search(
        self, question: str, *, top_k: int = 4
    ) -> list[tuple[KnowledgeDoc, float]]:
        """检索最相关的 top_k 条知识文档（按相似度降序）。"""
        if not question.strip():
            return []
        q = _bigrams(question)
        scored = [
            (doc, self._score(q, grams))
            for doc, grams in self._doc_grams
            if self._score(q, grams) >= 0
        ]
        scored = [(d, s) for d, s in scored if s >= _MIN_SCORE]
        scored.sort(key=lambda x: (-x[1], x[0].id))
        return scored[:top_k]


# 模块级单例（只读线程安全）
_index = KnowledgeIndex()


def search_knowledge(question: str, *, top_k: int = 4) -> list[tuple[KnowledgeDoc, float]]:
    """检索入口（供 service 层调用）。"""
    return _index.search(question, top_k=top_k)
