"""会话与 Agent 作业 API —— S2「作业表 + worker」「AG-01/AG-02」的 HTTP 面（ENT-011）。

会话
----
- GET    /api/v1/entrust/assignments/{aid}/session-context    会话上下文（该用哪条授权）
- POST   /api/v1/entrust/entrustments/{eid}/sessions        创建会话（幂等）
- GET    /api/v1/entrust/sessions                           列表（我的 / 授权的组织）
- GET    /api/v1/entrust/sessions/{sid}                     详情（含消息时间线）
- POST   /api/v1/entrust/sessions/{sid}/messages            追加操作者消息（幂等）
- POST   /api/v1/entrust/sessions/{sid}/archive             归档（幂等）

作业
----
- POST   /api/v1/entrust/sessions/{sid}/jobs                提交作业（幂等，不执行）
- GET    /api/v1/entrust/agent/jobs                         列表
- GET    /api/v1/entrust/agent/jobs/{jid}                   详情（含尝试日志）
- POST   /api/v1/entrust/agent/jobs/{jid}/run               worker 单步推进（幂等）
- POST   /api/v1/entrust/agent/jobs/{jid}/retry             显式重试（幂等）
- POST   /api/v1/entrust/agent/jobs/{jid}/cancel            取消（幂等）
- POST   /api/v1/entrust/agent/jobs/{jid}/adopt             采纳提案为成果（归属服务端派生，幂等）
- GET    /api/v1/entrust/agent/specialties                  专业槽位与开放状态

权限（AC-10，全部走 `authz.py` 这条唯一入口）
-----------------------------------------------
* 会话与作业都是**经理侧**能力：可见性按授权链（货主本人或授权组织成员）判定，
  否则 404；
* **读**会话需要 `entrust:view`；**写**（建会话/发消息/提交作业/推进/重试/取消）
  需要 `entrust:agent:job`（按货主作用域，由该货主的生效授权提供）；
* 只读成员能看到历史，但**不能**让 Agent 干活；
* 未挂授权的私有会话（`entrustment_id IS NULL`）按"归属方 + 组织"判定
  （`assert_can_view_scoped_object`）。

三个已登记但未开放的专业
------------------------
`GET /agent/specialties` 会把 AG-03/04/05 一并列出并标记 `open=false`，
让 UI-02 把它们显示为"后续开放"而不是隐藏 —— 用户需要知道"这个位置将来有什么"。
建会话或提作业时选择未开放专业会被 400 拒绝。

已知限制（如实记录）
--------------------
* **没有常驻 worker**：`POST /agent/jobs/{jid}/run` 是"推进一次"的显式入口，
  也供 CI 驱动整条链路；生产应由定时任务调用 `agentjobs.tick()`；
* 作业成功后只产出**信封（提案）**，不会自动写成果 —— 把提案变成成果有两条显式路径：
  `POST /entrustments/{eid}/artifacts`（直接创建）与 `POST /agent/jobs/{jid}/adopt`
  （采纳该作业的提案，归属由作业行派生）。两条路径都**必须由人工发起**（AC-09）；
* 附件文本提取（S1 第 6 条）未实现，因此 AG-02 目前以**操作者粘贴的报价文本**
  为输入；附件只作为来源引用被登记。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.modules.auth.dependencies import get_current_user
from app.modules.entrust import agentjobs as jobs_svc
from app.modules.entrust import artifacts as art
from app.modules.entrust import offers as offers_svc
from app.modules.entrust import sessions as sess_svc
from app.modules.entrust._http import (
    guard_or_400,
    map_access_denied,
    map_artifact_error,
    require_entrust_enabled,
    run_write,
)
from app.modules.entrust.access import (
    PERM_AGENT_JOB,
    PERM_QUOTE_CREATE,
    PERM_VIEW,
    find_active_entrustments,
)
from app.modules.entrust.authz import (
    assert_can_view_entrustment,
    assert_can_view_scoped_object,
    assert_can_write_entrustment,
    assert_org_member,
    load_assignment,
    load_assignment_for_entrustment,
    load_entrustment,
    not_found,
)
from app.modules.entrust.envelope import (
    SpecialtyNotOpenError,
    list_specialties,
)
from app.modules.entrust.schemas import (
    AgentJobDetailOut,
    AgentJobListOut,
    AgentJobOut,
    ArtifactAdoptIn,
    JobAttemptOut,
    JobCreate,
    SessionContextOut,
    SessionCreate,
    SessionDetailOut,
    SessionListOut,
    SessionMessageIn,
    SessionMessageOut,
    SessionOut,
    job_out,
    message_out,
    session_out,
)

router = APIRouter()

_SCOPE_SESSION_CREATE = "entrust:session:create"
_SCOPE_MESSAGE = "entrust:session:message"
_SCOPE_ARCHIVE = "entrust:session:archive"
_SCOPE_JOB_SUBMIT = "entrust:agent:job:submit"
_SCOPE_JOB_RUN = "entrust:agent:job:run"
_SCOPE_JOB_RETRY = "entrust:agent:job:retry"
_SCOPE_JOB_CANCEL = "entrust:agent:job:cancel"
_SCOPE_JOB_ADOPT = "entrust:agent:job:adopt"


def _map_errors(exc: Exception) -> HTTPException | None:
    """会话/作业异常 → HTTP 语义；无法识别返回 None（不吞真实 bug）。"""
    mapped = map_access_denied(exc)
    if mapped is not None:
        return mapped
    if isinstance(exc, (sess_svc.SessionNotFoundError, jobs_svc.AgentJobNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (sess_svc.SessionArchivedError, jobs_svc.AgentJobStateError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SpecialtyNotOpenError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (sess_svc.SessionError, jobs_svc.AgentJobError)):
        return HTTPException(status_code=400, detail=str(exc))
    return None


# ── 可见性与写权限 ──────────────────────────────────────────────────────────


def _session_write_guards(db: Session, *, user_id: int, session_row: dict[str, Any]) -> None:
    """会话/作业的**写**权限：挂在授权链上走授权链，否则按归属方 + 组织判定。"""
    entrustment_id = session_row.get("entrustment_id")
    if entrustment_id is not None:
        entrustment = load_entrustment(db, int(entrustment_id))
        if entrustment is None:
            raise not_found("会话不存在")
        assert_can_write_entrustment(
            db,
            user_id=user_id,
            permission=PERM_AGENT_JOB,
            entrustment=entrustment,
            detail="会话不存在",
        )
        return
    # 未挂授权的私有会话：只有创建者本人可写（组织成员也需显式授予 agent 作业权限）
    if user_id == int(session_row["created_by"]):
        return
    from app.modules.entrust.access import resolve_context

    ctx = resolve_context(db, user_id=user_id)
    assert_org_member(ctx, org_id=session_row.get("org_id"), detail="会话不存在")
    if not ctx.can(PERM_AGENT_JOB, owner_user_id=int(session_row["owner_user_id"])):
        raise HTTPException(status_code=403, detail="缺少发起 Agent 作业的权限")


def _visible_session(db: Session, *, user_id: int, session_id: int) -> dict[str, Any]:
    """读会话（含可见性判定）。不可见一律 404。"""
    try:
        session_row = sess_svc.get_session(db, session_id)
    except sess_svc.SessionError as exc:
        raise _map_errors(exc) or exc from exc
    entrustment_id = session_row.get("entrustment_id")
    if entrustment_id is not None:
        entrustment = load_entrustment(db, int(entrustment_id))
        if entrustment is None:
            raise not_found("会话不存在")
        assert_can_view_entrustment(db, user_id=user_id, entrustment=entrustment)
        return session_row
    assert_can_view_scoped_object(
        db,
        user_id=user_id,
        owner_user_id=int(session_row["owner_user_id"]),
        org_id=session_row.get("org_id"),
        detail="会话不存在",
    )
    return session_row


def _attempts_out(db: Session, job_id: int) -> list[JobAttemptOut]:
    """尝试日志的**白名单投影**：`raw_output`（未校验的模型原始输出）不外泄。

    原始输出只在服务端表里，供审计与复盘；把它返回给前端会诱导调用方直接使用它。
    """
    return [JobAttemptOut.model_validate(item) for item in jobs_svc.list_attempts(db, job_id)]


def _visible_job(db: Session, *, user_id: int, job_id: int) -> dict[str, Any]:
    """读作业（可见性随会话走）。"""
    try:
        job = jobs_svc.get_job(db, job_id)
    except jobs_svc.AgentJobError as exc:
        raise _map_errors(exc) or exc from exc
    if job.get("session_id") is not None:
        _visible_session(db, user_id=user_id, session_id=int(job["session_id"]))
        return job
    if job.get("entrustment_id") is not None:
        entrustment = load_entrustment(db, int(job["entrustment_id"]))
        if entrustment is None:
            raise not_found("作业不存在")
        assert_can_view_entrustment(db, user_id=user_id, entrustment=entrustment)
        return job
    if job.get("assignment_id") is not None:
        assignment = load_assignment(db, int(job["assignment_id"]))
        if assignment is None:
            raise not_found("作业不存在")
        if user_id == int(assignment["owner_user_id"]):
            return job
        assert_can_view_scoped_object(
            db,
            user_id=user_id,
            owner_user_id=int(assignment["owner_user_id"]),
            org_id=assignment.get("org_id"),
            detail="作业不存在",
        )
        return job
    raise not_found("作业不存在")


# ── 会话端点 ────────────────────────────────────────────────────────────────


@router.post(
    "/entrustments/{entrustment_id}/sessions",
    response_model=SessionOut,
    summary="创建会话（授权组织成员，可按专业槽位创建；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def create_session(
    entrustment_id: int,
    data: SessionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    entrustment = load_entrustment(db, entrustment_id)
    if entrustment is None:
        raise not_found("委托授权不存在")
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_AGENT_JOB,
        entrustment=entrustment,
        detail="会话不存在",
    )
    owner_user_id = int(entrustment["entrust_user_id"])
    org_id = int(entrustment["org_id"]) if entrustment.get("org_id") is not None else None

    assignment_id = data.assignment_id
    if assignment_id is not None and (
        # 委托单必须与授权**同属一个货主与组织**，否则会话会把两个上下文缝在一起。
        # 规则实现在 authz.load_assignment_for_entrustment（与成果归属共用一份）。
        load_assignment_for_entrustment(
            db,
            entrustment=entrustment,
            assignment_id=assignment_id,
            detail="委托单不属于该委托授权，不能创建会话",
        )
        is None
    ):
        raise HTTPException(status_code=400, detail="委托单不属于该委托授权，不能创建会话")

    payload = data.model_dump(mode="json")
    return run_write(
        db,
        scope=_SCOPE_SESSION_CREATE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: session_out(
            sess_svc.create_session(
                db,
                entrustment_id=entrustment_id,
                assignment_id=assignment_id,
                owner_user_id=owner_user_id,
                org_id=org_id,
                created_by=int(user.id),
                specialty=data.agent_specialty,
                title=data.title,
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.get(
    "/assignments/{assignment_id}/session-context",
    response_model=SessionContextOut,
    summary="会话上下文（为这单开会话该用哪条委托授权）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_session_context(
    assignment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """解析「为这张委托单创建会话」所需的授权上下文。

    ## 为什么需要它（这不是可选的便利端点）

    `POST /entrustments/{entrustment_id}/sessions` 要求调用方**先知道授权 id**，
    而现有两条清单都答不了这个问题：

    * `GET /my-orgs` 读的是 `ent_org_member`（组织成员身份），不含授权 id；
    * `GET /my-entrustments` 只列**货主自己授权出去**的授权 —— 经理不在其中。

    于是界面这边只剩下"猜一个授权 id、试到不报错为止"，而权限判定绝不能建立在
    猜测上。这与 `access.list_my_orgs` 的 docstring 是同一条论据：
    **服务端说"请指定"，前端就必须有合法手段拿到可选项**。

    ## 它做查找，不做判定

    * **做**：把这条委托单对应的 `(货主, 组织)` 与唯一一条匹配的生效授权查出来；
    * **不做**：不回答"你能不能建"。那个判断由
      `assert_can_write_entrustment` 唯一决定（见 `SessionContextOut` 的说明）。

    匹配不到唯一一条时**不猜**：返回 `entrustment_id=null` 并在 `note` 里写清是哪一种
    （未指定组织 / 无生效授权 / 多条匹配需显式指定）。
    """
    assignment = load_assignment(db, assignment_id)
    if assignment is None:
        raise not_found("委托不存在")
    owner_user_id = int(assignment["owner_user_id"])
    raw_org = assignment.get("org_id")
    org_id = int(raw_org) if raw_org is not None else None

    user_id = int(user.id)
    if user_id != owner_user_id:
        # 与委托详情、工作台**同一条**可见性口径（货主本人或授权组织成员），
        # 不在这里另立一套判据。
        assert_can_view_scoped_object(
            db,
            user_id=user_id,
            owner_user_id=owner_user_id,
            org_id=org_id,
            detail="委托不存在",
        )

    entrustment_id: int | None = None
    note = ""
    if org_id is None:
        # 没选定服务经营主体 ⇒ 无从定位授权。这不是"没权限"，报错文案要区分开。
        note = "该委托未指定服务经营主体，无法定位委托授权"
    else:
        # ⚠️ 这里刻意**不**用 `resolve_context().delegations` 过滤：那只覆盖
        # 调用者所在组织那一侧，货主本人会拿到空元组 —— 而"这单的货主与组织之间
        # 有哪几条生效授权"与"谁在问"无关（见 find_active_entrustments 的说明）。
        matches = find_active_entrustments(db, org_id=org_id, owner_user_id=owner_user_id)
        if len(matches) == 1:
            entrustment_id = matches[0]
        elif not matches:
            note = "这单的货主与组织之间没有生效中的委托授权"
        else:
            # 唯一选项才自动选中，多条一律不猜 —— 猜错的后果是把会话挂到
            # 另一条授权上，数据边界随之改变，而界面上看不出来。
            note = "同一(货主, 组织)下存在多条生效授权，需显式指定"

    return SessionContextOut(
        assignment_id=assignment_id,
        org_id=org_id,
        entrustment_id=entrustment_id,
        note=note,
    )


@router.get(
    "/sessions",
    response_model=SessionListOut,
    summary="会话列表（我的 / 授权组织的）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_sessions(
    view: str = Query(default="org", description="org=授权组织的会话；mine=我创建的"),
    org_id: int | None = Query(default=None, ge=1),
    session_status: str | None = Query(default=None, alias="status"),
    agent_specialty: str | None = Query(default=None),
    assignment_id: int | None = Query(
        default=None, ge=1, description="按委托单过滤（会话页从工作台进来时只有委托单号）"
    ),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    user_id = int(user.id)
    if view == "mine":
        total, items = sess_svc.list_sessions(
            db,
            created_by=user_id,
            agent_specialty=agent_specialty,
            status=session_status,
            assignment_id=assignment_id,
            page=page,
            size=size,
        )
    elif view == "org":
        from app.modules.entrust.access import resolve_context

        ctx = resolve_context(db, user_id=user_id)
        if org_id is not None:
            if org_id not in ctx.org_ids:
                return SessionListOut(total=0, page=page, size=size, items=[])
            scope_org = org_id
        elif len(ctx.org_ids) == 1:
            scope_org = next(iter(ctx.org_ids))
        elif not ctx.org_ids:
            return SessionListOut(total=0, page=page, size=size, items=[])
        else:
            raise HTTPException(
                status_code=400, detail="用户属于多个组织，请用 org_id 指定要查看的组织"
            )
        if not ctx.can(PERM_VIEW, org_id=scope_org):
            raise HTTPException(status_code=403, detail="缺少委托查看权限")
        total, items = sess_svc.list_sessions(
            db,
            org_id=scope_org,
            agent_specialty=agent_specialty,
            status=session_status,
            assignment_id=assignment_id,
            page=page,
            size=size,
        )
    else:
        raise HTTPException(status_code=422, detail="view 必须是 org 或 mine")

    return SessionListOut(total=total, page=page, size=size, items=[session_out(i) for i in items])


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetailOut,
    summary="会话详情（含消息时间线；不可见一律 404）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    session_row = _visible_session(db, user_id=int(user.id), session_id=session_id)
    return SessionDetailOut(
        session=session_out(session_row),
        messages=[message_out(m) for m in sess_svc.list_messages(db, session_id)],
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=SessionMessageOut,
    summary="追加操作者消息（角色固定 user；归档会话 409；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def append_message(
    session_id: int,
    data: SessionMessageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    session_row = _visible_session(db, user_id=int(user.id), session_id=session_id)
    _session_write_guards(db, user_id=int(user.id), session_row=session_row)

    payload = {"session_id": session_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_MESSAGE,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: message_out(
            sess_svc.append_message(
                db,
                session_id=session_id,
                # 角色与来源由服务端固定：调用方不能伪造 agent 消息
                role=sess_svc.ROLE_USER,
                content=data.content,
                source=sess_svc.SOURCE_MANUAL,
                created_by=int(user.id),
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.post(
    "/sessions/{session_id}/archive",
    response_model=SessionOut,
    summary="归档会话（归档后不再接受消息与作业；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def archive_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    session_row = _visible_session(db, user_id=int(user.id), session_id=session_id)
    _session_write_guards(db, user_id=int(user.id), session_row=session_row)
    return run_write(
        db,
        scope=_SCOPE_ARCHIVE,
        key=key,
        actor_user_id=int(user.id),
        payload={"session_id": session_id},
        business=lambda: session_out(
            sess_svc.archive_session(db, session_id=session_id, actor_id=int(user.id))
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


# ── 作业端点 ────────────────────────────────────────────────────────────────


@router.post(
    "/sessions/{session_id}/jobs",
    response_model=AgentJobOut,
    summary="提交 Agent 作业（提交与执行分离；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def submit_job(
    session_id: int,
    data: JobCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    session_row = _visible_session(db, user_id=int(user.id), session_id=session_id)
    _session_write_guards(db, user_id=int(user.id), session_row=session_row)
    if session_row.get("status") == sess_svc.STATUS_ARCHIVED:
        raise HTTPException(status_code=409, detail="会话已归档，不能提交作业")
    if not session_row.get("agent_specialty"):
        raise HTTPException(status_code=400, detail="该会话是通用会话壳，没有可用的专业能力")

    payload = {"session_id": session_id, **data.model_dump(mode="json")}
    return run_write(
        db,
        scope=_SCOPE_JOB_SUBMIT,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=lambda: job_out(
            jobs_svc.submit_job(
                db,
                session_id=session_id,
                entrustment_id=session_row.get("entrustment_id"),
                assignment_id=session_row.get("assignment_id"),
                task_id=data.task_id,
                artifact_id=data.artifact_id,
                specialty=str(session_row["agent_specialty"]),
                base_revision=data.base_revision,
                job_input=data.input,
                max_attempts=data.max_attempts or jobs_svc.DEFAULT_MAX_ATTEMPTS,
                created_by=int(user.id),
            )
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.get(
    "/agent/specialties",
    summary="专业槽位与开放状态（未开放的也要列出，UI 标注为后续开放）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_specialties(user: User = Depends(get_current_user)) -> Any:
    """静态契约：任何登录用户都能读到"有哪五个专业、哪些本期开放"。"""
    return {"items": list_specialties()}


@router.get(
    "/agent/jobs",
    response_model=AgentJobListOut,
    summary="作业列表（按会话 / 委托单 / 状态过滤）",
    dependencies=[Depends(require_entrust_enabled)],
)
def list_jobs(
    session_id: int | None = Query(default=None, ge=1),
    assignment_id: int | None = Query(default=None, ge=1),
    job_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    if session_id is not None:
        _visible_session(db, user_id=int(user.id), session_id=session_id)
    elif assignment_id is not None:
        assignment = load_assignment(db, assignment_id)
        if assignment is None:
            raise not_found("委托单不存在")
        if int(user.id) != int(assignment["owner_user_id"]):
            assert_can_view_scoped_object(
                db,
                user_id=int(user.id),
                owner_user_id=int(assignment["owner_user_id"]),
                org_id=assignment.get("org_id"),
                detail="委托单不存在",
            )
    else:
        raise HTTPException(status_code=422, detail="必须指定 session_id 或 assignment_id")

    total, items = jobs_svc.list_jobs(
        db,
        session_id=session_id,
        assignment_id=assignment_id,
        status=job_status,
        page=page,
        size=size,
    )
    return AgentJobListOut(total=total, page=page, size=size, items=[job_out(i) for i in items])


@router.get(
    "/agent/jobs/{job_id}",
    response_model=AgentJobDetailOut,
    summary="作业详情（含信封与尝试日志；原始模型输出不外泄）",
    dependencies=[Depends(require_entrust_enabled)],
)
def get_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    job = _visible_job(db, user_id=int(user.id), job_id=job_id)
    return AgentJobDetailOut(job=job_out(job), attempts=_attempts_out(db, job_id))


@router.post(
    "/agent/jobs/{job_id}/run",
    response_model=AgentJobDetailOut,
    summary="推进一次（worker 单步；无常驻进程时由定时任务调用）",
    dependencies=[Depends(require_entrust_enabled)],
)
async def run_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """推进一次作业。

    **本端点不要求 `Idempotency-Key`**，这是有意的：它的幂等保证来自作业状态机本身
    —— 重复调用时 `claim_job` 会因"已结束"或"租约仍被持有"返回 `None`（→ 409），
    连一次尝试都不会多消耗。这比"用键重放旧响应"更强：键重放只防重复提交，
    而这里防的是重复**执行**。

    没有常驻进程时，生产由定时任务调用 `agentjobs.tick()` 批量推进。
    """
    job = _visible_job(db, user_id=int(user.id), job_id=job_id)
    if job.get("session_id") is not None:
        session_row = _visible_session(db, user_id=int(user.id), session_id=int(job["session_id"]))
        _session_write_guards(db, user_id=int(user.id), session_row=session_row)
    else:
        # 无会话作业（由内部流程创建）只有发起人可推进
        if int(user.id) != int(job["created_by"]):
            raise HTTPException(status_code=403, detail="缺少推进该作业的权限")

    worker_id = f"user:{user.id}"
    claimed = jobs_svc.claim_job(
        db, job_id=job_id, worker_id=worker_id, lease_seconds=jobs_svc.DEFAULT_LEASE_SECONDS
    )
    if claimed is None:
        raise HTTPException(
            status_code=409, detail="作业不可推进（已结束、已被其他 worker 领取或额度用尽）"
        )
    scope = jobs_svc.scope_for_job(db, claimed, operator_user_id=int(user.id))
    result = await jobs_svc.execute_claimed_job(
        db,
        job_id=job_id,
        scope=scope,
        worker_id=worker_id,
        attempt_no=int(claimed["attempt_count"]),
    )
    return AgentJobDetailOut(job=job_out(result), attempts=_attempts_out(db, job_id))


@router.post(
    "/agent/jobs/{job_id}/retry",
    response_model=AgentJobOut,
    summary="显式重试失败作业（重置尝试计数；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def retry_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    job = _visible_job(db, user_id=int(user.id), job_id=job_id)
    if job.get("session_id") is not None:
        session_row = _visible_session(db, user_id=int(user.id), session_id=int(job["session_id"]))
        _session_write_guards(db, user_id=int(user.id), session_row=session_row)
    elif int(user.id) != int(job["created_by"]):
        raise HTTPException(status_code=403, detail="缺少重试该作业的权限")
    return run_write(
        db,
        scope=_SCOPE_JOB_RETRY,
        key=key,
        actor_user_id=int(user.id),
        payload={"job_id": job_id},
        business=lambda: job_out(
            jobs_svc.retry_job(db, job_id=job_id, actor_id=int(user.id))
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


@router.post(
    "/agent/jobs/{job_id}/cancel",
    response_model=AgentJobOut,
    summary="取消作业（已结束的作业 409；幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def cancel_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    key = guard_or_400(idempotency_key)
    job = _visible_job(db, user_id=int(user.id), job_id=job_id)
    if job.get("session_id") is not None:
        session_row = _visible_session(db, user_id=int(user.id), session_id=int(job["session_id"]))
        _session_write_guards(db, user_id=int(user.id), session_row=session_row)
    elif int(user.id) != int(job["created_by"]):
        raise HTTPException(status_code=403, detail="缺少取消该作业的权限")
    return run_write(
        db,
        scope=_SCOPE_JOB_CANCEL,
        key=key,
        actor_user_id=int(user.id),
        payload={"job_id": job_id},
        business=lambda: job_out(
            jobs_svc.cancel_job(db, job_id=job_id, actor_id=int(user.id))
        ).model_dump(mode="json"),
        map_domain_error=_map_errors,
    )


# ── 提案采纳（DR-0012）──────────────────────────────────────────────────────


def _proposed_artifact_types(job: dict[str, Any]) -> set[str]:
    """作业信封里**实际提出过**的成果类型（提案 ≠ 成果，这里只读不写）。"""
    envelope = job.get("envelope") or {}
    proposals = envelope.get("artifact_proposals") or []
    return {
        str(item["artifact_type"])
        for item in proposals
        if isinstance(item, dict) and item.get("artifact_type")
    }


@router.post(
    "/agent/jobs/{job_id}/adopt",
    summary="采纳作业提案为成果（归属由服务端派生，source=agent，幂等）",
    dependencies=[Depends(require_entrust_enabled)],
)
def adopt_job_proposal(
    job_id: int,
    data: ArtifactAdoptIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    """把一份通过校验的 Agent 提案**变成成果**（DR-0012 的「Agent 采纳」入口）。

    为什么归属必须由服务端派生
    --------------------------
    "具体委托内的新成果必须有有效归属"这句话只有在**服务端知道上下文**时才能被
    强制：如果归属由请求体声明，调用方可以不声明，规则就退化成"自愿遵守"。
    这里归属取自**作业行自己记录的 `assignment_id`**（提交作业时从会话继承），
    请求体里根本没有这个字段 —— 调用方无从绕过。

    权限与前置
    ----------
    * 可见性：`_visible_job`（随会话/委托/委托单走），不可见 404；
    * 写权限：`entrust:quote:create`，按**该作业所属授权**的货主作用域判定
      （采纳等于创建成果，权限也就等于创建成果的权限，不新造权限码）；
    * 作业必须是 `succeeded` —— 只有成功作业才有通过校验的 `envelope_json`；
    * `artifact_type` 必须是该作业**确实提出过**的类型，否则 400（防张冠李戴）。

    已知限制（如实记录，不假装已解决）
    ----------------------------------
    * 没有 `entrustment_id` 的作业（只挂委托单或私有会话）**不能采纳**：成果表的
      `entrustment_id` 仍为 NOT NULL，而从 (货主, 组织) 反推授权会猜错（同一
      (org, owner) 可能有多条授权记录）。这类作业走 400 并被明确告知原因；
    * **防重复采纳只靠幂等键**：同一提案用**不同**幂等键再次采纳会再产生一份成果。
      要按 (job, artifact_type) 硬去重，需要在成果上记录"来自哪个作业哪份提案"的
      来源列 —— 那是独立增量，本次不做，列在 DR-0012 的未完成项里。
    """
    key = guard_or_400(idempotency_key)
    job = _visible_job(db, user_id=int(user.id), job_id=job_id)
    entrustment_id = job.get("entrustment_id")
    if entrustment_id is None:
        raise HTTPException(
            status_code=400,
            detail="该作业没有关联委托授权，无法确定成果归属（成果必须挂在生效授权下）",
        )
    entrustment = load_entrustment(db, int(entrustment_id))
    if entrustment is None:
        raise not_found("成果不存在")
    assert_can_write_entrustment(
        db,
        user_id=int(user.id),
        permission=PERM_QUOTE_CREATE,
        entrustment=entrustment,
        detail="成果不存在",
    )
    if job.get("status") != jobs_svc.STATUS_SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail=f"作业状态为 {job.get('status')}，只有 succeeded 的作业有可采纳的提案",
        )
    artifact_type = data.artifact_type.strip()
    proposed = _proposed_artifact_types(job)
    if artifact_type not in proposed:
        raise HTTPException(
            status_code=400,
            detail=f"该作业没有提出 {artifact_type!r} 类型的提案（实际提案：{sorted(proposed)}）",
        )
    source_assignment_id = job.get("assignment_id")
    payload = {"job_id": job_id, **data.model_dump(mode="json")}

    def _adopt() -> dict[str, Any]:
        """采纳为成果，并把**信封里声明过的来源**记成待核验项。

        ⚠️ 为什么这一步必须在采纳时做，而不是等到发布时现查：来源只存在于**作业信封**，
        而成果/版本表**没有任何溯源列**（DR-0012 未完成项）。不在采纳时把"模型当初声明了
        什么"落到台账上，发布门槛就只剩下一个没有对象的勾选 —— 那是假门槛。

        `artifacts.create_artifact` 内部自己 commit ⇒ 台账写入是**紧随其后**的第二次提交，
        两者不在一个事务里。因此门槛把"模型产出却一条声明都没有"判为**不得发布**
        （`offers.source_gate` 的 `missing_declaration`）—— 那条缝由此变成一句可读的拒绝理由，
        而不是一次静默放行。

        来源**标注**（`ent_artifact_origin`）在这里一并写下，依据是**作业行的 `mocked`**：
        这个标志由服务端在发起调用时写入（`agentjobs` 的三态投影），调用方无从伪造。
        合同 §11.1 要求 "labeled synthetic/manual/live sources" —— 标注必须来自事实，
        让经理自己勾选"这次是 live"等于把标注变成一句口号。
        """
        created = art.create_artifact(
            db,
            entrustment_id=int(entrustment_id),
            artifact_type=artifact_type,
            payload=data.payload,
            created_by=int(user.id),
            source=art.SOURCE_AGENT,
            note=data.note,
            assignment_id=(int(source_assignment_id) if source_assignment_id is not None else None),
        )
        envelope = job.get("envelope") or {}
        declared = [
            {"kind": str((r or {}).get("kind") or ""), "ref": str((r or {}).get("ref") or "")}
            for r in (envelope.get("source_refs") or [])
        ]
        written = offers_svc.record_declared_sources(
            db,
            artifact_id=int(created["artifact_id"]),
            revision_no=1,  # 采纳产出的成果首版本即 revision_no=1
            sources=declared,
        )
        if written:
            db.commit()
        # 三态：`True`＝桩/夹具，`False`＝真实调用，`None`＝没有尝试行 ⇒ **不猜**，标 unknown。
        mocked = job.get("mocked")
        if mocked is True:
            mode = offers_svc.ORIGIN_SYNTHETIC
        elif mocked is False:
            mode = offers_svc.ORIGIN_LIVE
        else:
            mode = offers_svc.ORIGIN_UNKNOWN
        offers_svc.record_origin(
            db,
            artifact_id=int(created["artifact_id"]),
            revision_no=1,
            mode=mode,
            basis={"job_id": int(job_id), "job_mocked": mocked},
            actor_user_id=int(user.id),
        )
        return created

    return run_write(
        db,
        scope=_SCOPE_JOB_ADOPT,
        key=key,
        actor_user_id=int(user.id),
        payload=payload,
        business=_adopt,
        map_domain_error=map_artifact_error,
    )
