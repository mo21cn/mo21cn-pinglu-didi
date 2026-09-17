"""ENT-010：声明式权限矩阵（`scope_matrix`）的自检。

这组断言不是"再测一遍业务"，而是让**结构性错误无法悄悄进入 develop**：

1. **双向覆盖** —— 新增端点不声明 → 失败；声明了不存在的端点 → 失败。
2. **权限常量有效性** —— 引用 `access` 里不存在的权限 → 失败。
3. **写端点幂等完备** —— 写端点既不声明幂等、也不进豁免表 → 失败。
4. **开关关闭全 404** —— `ENTRUST_ENABLED=false` 时**任何**端点仍可达 → 失败
   （AC-22 的结构性版本：此前只覆盖"三组端点"，现在覆盖全部，且新增端点自动纳入）。

## 本文件**不**做什么

不检查"某行代码里是否出现 `assert_can`"。守卫可以合法地落在端点、模块内助手
（如 `agent_api._session_write_guards`）或服务层（如 `tasks.authorize`），
源码匹配会随正当重构漂移 —— 那会把一次正确的重构判成失败，进而训练团队绕过门禁。
`note` 里记录了守卫落点供人审查，但它不是断言依据。
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.modules.entrust import access as access_mod
from app.modules.entrust import scope_matrix as sm

# ── 1. 双向覆盖 ────────────────────────────────────────────────────────────


def _openapi_entrust_routes() -> set[tuple[str, str]]:
    """从 openapi 取委托支线的全部 (方法, 相对路径)。

    本项目的 FastAPI 用 `_IncludedRouter` 惰性挂载，直接遍历 `app.routes` 只能拿到
    占位对象、拿不到子路由，所以必须走 `app.openapi()`（它会展开全部路由）。
    """
    from app.main import app

    prefix = sm.API_PREFIX
    routes: set[tuple[str, str]] = set()
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix) :] or "/"
        for method in ops:
            routes.add((method.upper(), relative))
    return routes


def test_matrix_covers_every_exposed_route():
    """openapi 暴露的每条路由都必须在矩阵里声明。"""
    missing = sorted(_openapi_entrust_routes() - set(sm.matrix_keys()))
    assert not missing, (
        f"以下端点已暴露但未在 scope_matrix.SCOPE_MATRIX 声明：{missing}。"
        "新增端点必须声明 scope（guard / permission / owner_scope / idempotent / note）。"
    )


def test_matrix_has_no_stale_entries():
    """矩阵里不能有已不存在的端点（僵尸条目会让人以为还有这条接口）。"""
    stale = sorted(set(sm.matrix_keys()) - _openapi_entrust_routes())
    assert not stale, f"scope_matrix 声明了不存在的端点：{stale}"


def test_matrix_keys_are_unique():
    keys = [sm.scope_key(item.method, item.path) for item in sm.SCOPE_MATRIX]
    assert len(keys) == len(set(keys)), "矩阵存在重复条目"


def test_matrix_size_matches_baseline():
    """条目数锁定为 63。

    数量变化本身不是错误，但**必须是有意的**：增删端点时同时改这里，
    强制在 PR 里显式说明"为什么端点集合变了"。

    48 → 49（ENT-012 第二切片）：新增 `GET /my-orgs` —— 组织选择器的数据源。
    49 → 51（ENT-020 成果归属切片 / DR-0012）：新增
    `GET /assignments/{assignment_id}/artifacts`（单委托成果清单）与
    `POST /agent/jobs/{job_id}/adopt`（采纳作业提案为成果）。
    51 → 52（ENT-021 工作台首片 / DR-0010）：新增
    `GET /assignments/{assignment_id}/workbench`（UI-05 七槽位摘要投影）。
    52 → 60（ENT-030 异常与变更案件 / DR-0013 A1）：新增 8 条 ——
    登记案件、列案件、案件详情、登记/移除受影响项、记录决定、关闭、重开。
    `exceptions` 槽位此前是本支线**唯一**仍「本期未开放」的槽位，这 8 条是它开放的结构前提。
    60 → 61（ENT-033 A2 五之一）：新增 `POST /exceptions/{exception_id}/apply`（应用已批准变更）。
    61 → 62（S1 客户受理 / DEMO-1 §3.1）：新增 `GET /my-entrustments` ——
    货主侧"我授权出去的组织"，是 UI-07 选择**提交目标**的唯一合法数据源。
    `ent_entrustment` 此前无任何 HTTP 面，`/my-orgs` 读的是 `ent_org_member`，
    两者不是一回事（DR-0012），拿后者顶上会产出"能选但必然 403"的选项。
    62 → 63（S2 首片 / HO 0917-2 执行顺序 2）：新增
    `GET /assignments/{assignment_id}/session-context` —— 建会话要
    `entrustment_id`，而 `/my-orgs` 只回成员身份、`/my-entrustments` 只回货主自己
    授权出去的授权，经理两边都拿不到本单那一条。缺了它，前端只剩"猜一个 id 试到不报错
    为止"，而权限判定不能建立在猜测上（与 61→62 那条同源，都是"服务端说请指定、
    前端必须有合法手段拿到可选项"）。
    63 → 71（**S3 纵向切片**：发布 → 冻结内容 → 客户响应）：新增 8 条 ——
    发布指定成果版本（1）、按授权列发布记录（2）、客户列"我收到的发布"（3）、
    按身份给发布详情（4）、客户响应（5）、撤回发布（6）、来源台账读（7）与写（8）。
    ⚠️ 这批端点的**投影分叉**（经理 / 客户两条通道）由服务端决定，不靠前端藏字段；
    其中 `POST /offer-releases/{id}/responses` 是**唯一**一条"身份即权限"的写端点
    （货主本人；经理来调一律 403，不是 404 —— 他看得见，但无权替客户确认）。
    """
    assert len(sm.SCOPE_MATRIX) == 71


# ── 2. 声明本身的自洽性 ────────────────────────────────────────────────────


def test_declared_permissions_exist_in_access_module():
    known = {v for k, v in vars(access_mod).items() if k.startswith("PERM_") and isinstance(v, str)}
    unknown = sorted(sm.declared_permissions() - known)
    assert not unknown, f"矩阵引用了 access 模块里不存在的权限：{unknown}"


def test_guards_are_known():
    unknown = sorted({item.guard for item in sm.SCOPE_MATRIX} - sm.GUARDS)
    assert not unknown, f"矩阵使用了未定义的 guard：{unknown}"


def test_every_route_has_a_note():
    """每条都要有一句授权语义说明 —— 空 note 等于没声明。"""
    missing = [f"{i.method} {i.path}" for i in sm.SCOPE_MATRIX if not i.note.strip()]
    assert not missing, f"这些条目没有 note：{missing}"


def test_authorized_guards_declare_a_permission():
    """「经授权」类 guard 必须给出具体权限，否则等于把判定推给实现细节。"""
    weak = [
        f"{i.method} {i.path}"
        for i in sm.SCOPE_MATRIX
        if i.guard in (sm.GUARD_ENTRUSTMENT_WRITE,) and i.permission is None
    ]
    assert not weak, f"声明为 entrustment_write 却没给 permission：{weak}"


# ── 3. 写端点幂等完备 ─────────────────────────────────────────────────────


def test_write_routes_are_idempotent_or_exempt():
    offenders = [f"{i.method} {i.path}" for i in sm.write_routes_without_idempotency()]
    assert not offenders, (
        f"以下写端点既未声明 idempotent、也不在 IDEMPOTENT_EXEMPT：{offenders}。"
        "写操作重复提交会产生重复副作用，必须显式处理（加幂等键，或写清为什么不需要）。"
    )


def test_exemptions_have_reasons_and_are_real_routes():
    for key, reason in sm.IDEMPOTENT_EXEMPT.items():
        assert reason.strip(), f"豁免 {key} 没写理由"
        assert key in sm.matrix_keys(), f"豁免 {key} 不是矩阵里的真实端点"


def test_exemptions_are_not_redundant():
    """已声明幂等的端点不该再进豁免表 —— 两边都写会让人以为幂等是「可选」的。"""
    redundant = [k for k in sm.IDEMPOTENT_EXEMPT if (item := sm.describe(*k)) and item.idempotent]
    assert not redundant, f"这些端点已声明幂等，不该再进豁免表：{redundant}"


# ── 4. 开关关闭时全部端点不可达 ────────────────────────────────────────────


def _fill_path_params(path: str) -> str:
    """把 `{param}` 替换成具体值 1（只为拼出可请求的 URL）。"""
    return re.sub(r"\{[^}]+\}", "1", path)


def test_all_routes_return_404_when_switch_off(client: TestClient, monkeypatch):
    """`ENTRUST_ENABLED=false` → **全部**端点 404。

    `require_entrust_enabled` 是 FastAPI 依赖，在请求体解析**之前**执行，
    所以写端点不需要构造合法请求体也能观察到 404。
    """
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)

    bad: list[str] = []
    for item in sm.SCOPE_MATRIX:
        url = sm.API_PREFIX + _fill_path_params(item.path)
        payload = {} if item.method in sm.WRITE_METHODS else None
        resp = client.request(item.method, url, json=payload)
        if resp.status_code != 404:
            bad.append(f"{item.method} {item.path} -> {resp.status_code}")
    assert not bad, f"开关关闭时这些端点没有返回 404：{bad}"


def test_switch_off_does_not_hide_other_modules(client: TestClient, monkeypatch):
    """反向确认：开关只影响委托支线，不该把别的模块一起关掉。"""
    monkeypatch.setattr(get_settings(), "ENTRUST_ENABLED", False)
    resp = client.get("/healthz")
    assert resp.status_code == 200
