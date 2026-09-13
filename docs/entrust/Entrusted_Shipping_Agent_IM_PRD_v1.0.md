# Entrusted Shipping Agent IM — Executable Requirements v1.0

> Execution owner: **Codex**  
> Product decision owner and acceptance reviewer: **Human Operator (HO)**  
> Product: 船好多 / Pinglu Didi  
> Document date: 2026-09-12  
> Document status: Implementation-ready requirements; not a software release certification  
> Repository: https://github.com/mo21cn/mo21cn-pinglu-didi  
> Verified reference baseline: **v0.5.1**, commit `0a2bbed08251dfe78e7d8b10202d1420003c1212`  
> Suggested implementation milestone: **v0.6.0 candidate**; assign a release tag only after acceptance  
> Language: English implementation specification; **Simplified Chinese product UI**

## 0. Decision and execution brief

**Build a human freight-manager workspace for entrusted shipping, using a main assistant, a navigation drawer, specialist Agent IM sessions, and a structured delivery workbench.**

The manager organizes the complete assignment. AI extracts information, proposes actions, prepares artifacts, checks consistency, and creates internal reminders. Explicitly authorized business commands update authoritative records. The first release does not autonomously promise carriage, book external capacity, sign contracts, pay suppliers, or file customs declarations.

The workflow is:

**受理委托 → 拆分任务 → 询价采购 → 形成方案 → 落实运输与交接 → 处理异常 → 对账结案**

These are connected work areas, not a mandatory linear wizard. Work may begin with an uploaded quote, an incomplete assignment, or a manually recorded transport event. Dependencies govern consequential transitions. A manager can intervene at every stage.

### 中文执行摘要

- 默认经理人入口为主助手首页；侧栏进入委托、Agent、历史会话或工作台。
- 每张委托拥有共享的任务、方案、报价、合同、交接、异常和结算记录。
- Agent IM 与工作台引用同一对象；聊天可生成草稿，工作台可直接人工完成业务。
- 货主端继续从“委托发货”进入客户流程；经理人端按经营主体成员权限进入。
- 首版支持单水运段及人工组织的多段运输，形成“一单制”协同基础；不宣称已获得真实联运资源或具备无人承运能力。
- 必须交付可运行的纵向闭环、真实数据库记录、权限隔离、版本和变更联动，以及可复现验收证据。

### Instructions to the implementing Codex

1. Inspect the current repository, applicable `AGENTS.md` if present, and existing contribution/testing conventions. Compare HEAD with the reference baseline; preserve subsequent legitimate work and user changes.
2. Implement in an isolated branch/worktree where appropriate. This document specifies development scope; it does not authorize production deployment, live external messages, financial transactions, or automatic merging.
3. Proceed through the milestones below without requiring HO to decide routine engineering choices. Record decisions and justified deviations in the delivery report.
4. Maintain a runnable vertical slice throughout development. Do not deliver only UI mockups, disconnected chatbots, schema files, or a future-work plan.
5. No sub-agent delegation is required by this document. Use the available execution environment and applicable permissions.
6. If external credentials are unavailable, complete the internal/manual workflow and deterministic fixtures. Label unavailable integrations accurately; do not simulate external acceptance as a real event.

## 1. Product objective and evidence boundary

### 1.1 Objective

Enable a freight manager to receive, organize, execute, and close an entrusted-shipping assignment through AI-assisted conversation or direct structured operations. The same assignment must support shipper-facing service, manager-facing procurement, and evidence-backed operational tracking.

The first release should answer four questions at any task:

1. What information or confirmation is missing?
2. Who owns the next action?
3. What evidence establishes completion?
4. What happens when the task is late, fails, or changes?

### 1.2 What the baseline actually provides

The reference implementation contains a WeChat miniapp, FastAPI services, SQLAlchemy models, deterministic ship/cargo matching, order and payment state machines, berth appointment logic, and AI parsing/FAQ/contract/compliance endpoints.

Important baseline constraints:

- The entrusted-shipping button routes to `pages/preview/preview` and has no business backend.
- Existing Order binds one cargo to one ship. Active-order protection is cargo-based, not a confirmed ship-schedule reservation.
- Matching uses static attributes and a home-port proximity approximation; it does not establish carrier acceptance, real-time position, route feasibility, or live capacity.
- Agent routing is single-hop intent dispatch. Agent calls are audited, but no durable assignment task orchestration exists.
- Contract generation produces unpersisted drafts; live electronic signature is not implemented.
- Payment success/refund behavior is simulated. Do not use these states as proof of real funds movement.
- Compliance screening advises but does not enforce all business restrictions.
- Port configuration is centered on Guangxi. Do not claim national waterway coverage or verified navigation conditions.

These are implementation boundaries, not reasons to redesign the whole application.

### 1.3 Baseline source anchors

All paths below refer to the pinned reference commit:

| Concern | Repository path |
| --- | --- |
| Entrusted entry | `miniapp/pages/publish/cargo/cargo.js` |
| Existing chat shell | `miniapp/pages/assistant/assistant.js` |
| Matching | `backend/app/modules/match/engine.py` |
| Order model and mutations | `backend/app/models/order.py`, `backend/app/modules/order/service.py` |
| Current role enforcement | `backend/app/modules/order/router.py` |
| Agent endpoints and routing | `backend/app/modules/agent/router.py`, `intent.py`, `service.py` |
| Model gateway and knowledge | `backend/app/modules/agent/llm.py`, `knowledge.py` |
| Compliance and contract kernels | `backend/app/modules/agent/compliance.py`, `contract.py` |
| Berth capacity pattern | `backend/app/modules/port/service.py` |
| Payment limitations | `backend/app/modules/payment/service.py` |
| Delivery description | `CHANGELOG.md`, `docs/汇报演示脚本.md` |

## 2. Scope and priorities

All items marked REQUIRED are first-release acceptance requirements. OPTIONAL items must not displace required work.

| Area | First-release scope |
| --- | --- |
| REQUIRED: customer journey | Draft, submit, view assignment, accept/reject exact proposal versions, submit change requests, confirm receipt, review customer settlement |
| REQUIRED: manager workspace | Main assistant, drawer, specialist conversations, portfolio workbench, assignment workbench, direct artifact editing |
| REQUIRED: workflow | Tasks, prerequisites, parallel work, due dates, assignment/reassignment, manual completion, reopen, exception handling |
| REQUIRED: artifacts | Plans, supplier quotes, customer offers, contract drafts/evidence, procurement records, handovers, exceptions, receivables/payables and settlement |
| REQUIRED: Agent behavior | Five specialties, structured outputs, provenance, missing-data reporting, task-bound context, persistent job state, deterministic fallback fixtures |
| REQUIRED: documents | Upload/store/download evidence; text/CSV and text-based PDF extraction; manual transcription path for unsupported or scanned documents |
| REQUIRED: governance | Organization and assignment isolation, scoped commands, revision checks, immutable accepted snapshots, audit, internal/customer visibility |
| REQUIRED: operations | Manual real-world confirmation capture with evidence; distinguish pending request, recorded confirmation, and verified integration receipt |
| OPTIONAL | Voice input, OCR for scans/images, push notification channels, supplier self-service confirmation links, additional specialty Agents |
| DEFERRED | Live customs filing, electronic-signature provider, live payment/splitting, AIS/lock/port integration, autonomous external procurement, national route optimizer, financing/negotiable electronic title |

Scanned images must remain usable as attachments even when OCR is deferred. Provide preview/download and editable extracted/manual fields; never discard evidence because extraction failed.

## 3. Actors, entry points, and operating identity

### 3.1 Actors

| Actor | Access and responsibility |
| --- | --- |
| Customer / shipper | Own assignments and explicitly released customer artifacts; confirmations and change requests |
| Freight manager | Assigned organizational work; internal quotes, costs, tasks, artifacts, and operational decisions |
| Organization administrator | Manage manager membership, assignment routing/reassignment, organization templates and allowed counterparties |
| Supplier / shipowner | Existing self-dispatch role remains; no new universal supplier portal is required |
| Agent service | Scoped technical identity using the initiating user's permissions and task context; no shared human login token |

An operating organization is distinct from its manager. Record the service provider, customer, actual carriers, and manager separately. Do not infer carrier responsibility from an AI avatar or platform branding.

### 3.2 Entry behavior

- Preserve **自主发货** and all existing shipper/owner workflows.
- **委托发货** opens the customer intake page, never the manager's internal workspace.
- Customers select a configured eligible operating organization. With exactly one eligible organization, preselect it and display its identity. With none, allow saving a draft and display that submission is unavailable.
- Submission enters an organization queue. An organization administrator or eligible manager claims/assigns it using an atomic operation; simultaneous claims cannot silently overwrite each other.
- Provision manager membership through an authenticated administration path or documented administration command. Seeded demo membership is acceptable; customers must not self-assign this permission.
- Eligible users see **货代工作空间** in the existing account area. Manager workspace has its own navigation; leaving returns to the existing app.
- If the platform itself is not the identified service provider, replace the blanket customer text “平台承运” with wording that names the selected operating organization. Contract role is explicit per assignment.

### 3.3 Permission model

Use organization membership plus assignment access, not only the mutable `current_role` field. Every API, job, attachment download, artifact query, and conversation-context builder enforces this boundary.

Manager assignment access may be owner/editor or viewer. Organization administrators may reassign within their organization. Customers receive a field-allowlisted projection: internal procurement prices, margin, internal conversations, and supplier comparisons must not appear in their responses or exports.

## 4. Information architecture and pages

The HO-provided workflow image is an interaction reference: center main assistant, left drawer, specialist conversation, right structured workbench. Use the existing 船好多 visual language. Do not copy medical branding, clinician credentials, promotional panels, or health-specific functionality.

### 4.1 Page contracts

| ID | Page/surface | Required contents and behavior |
| --- | --- | --- |
| UI-01 | Manager assistant home | Organization identity; all-assignments/current-assignment selector; due/blocked/exception summaries; actionable suggestions; input and attachment control; persistent workbench button |
| UI-02 | Navigation drawer | Assignment search/list; five Agent entries; grouped recent conversations; workbench; organization switch; return to existing app |
| UI-03 | Specialist Agent IM | Visible assignment/task context; specialty label; messages; attachment context; artifact cards; running/failed jobs; manual takeover; link to exact workbench artifact |
| UI-04 | Portfolio workbench | Needs-my-action, awaiting-external-response, overdue, exceptions, unassigned intake where permitted; filters by manager/customer/stage; each item opens its exact target |
| UI-05 | Assignment workbench | Assignment summary; next actions; stable business slots; versions, dependencies, history, direct edit and Agent entry |
| UI-06 | Artifact detail/editor | Structured fields; source evidence; version comparison; review indicators; confirm/reject/share/record-execution actions allowed for that artifact type |
| UI-07 | Customer intake/detail | Draft/submit; selected provider; released plan/offer/contract; customer confirmations; progress; change requests; customer-only settlement |
| UI-08 | Exception/change detail | Cause, affected records, proposed resolution, approvals, implementation evidence, closure; accessible from workbench and chat |

UI-02 is a drawer/component, not necessarily a separate page. UI-06 can be a reusable detail page with typed forms. The implementing developer may select actual routes consistent with the repository; maintain a route registry and working back/deep-link behavior.

### 4.2 Home behavior

Support these initial queries without forcing the user through an assignment list first:

- “今天哪些委托需要我处理？” — read across authorized assignments.
- “帮我看这份船东报价。” — create a private unbound document/quote draft; require assignment binding before it affects business records.
- “继续处理昨天那批钢材。” — show matching authorized assignments when ambiguous.

Never silently pick between plausible assignments. Switching context visibly changes the title/context chip and cancels or isolates unsent task context. Existing jobs retain their original binding. A conversation that already produced task-bound business artifacts must not be rebound to a different assignment; start a new linked conversation.

Home quick actions: **新建委托、上传报价、整理单证、查看待办**. AI failure must not disable these direct workflows.

### 4.3 Assignment workbench slots

| Slot | Contents |
| --- | --- |
| 委托概况 | Customer/provider, cargo, origin/destination, scope, dates, assigned manager, missing information |
| 方案与任务 | Plan versions, legs, operation tasks, dependencies, progress |
| 采购与报价 | Supplier quotes, comparisons, validity, procurement confirmation, internal costs |
| 对客方案与合同 | Customer offers and acceptance, contract versions and signature/evidence status |
| 履约与交接 | Planned/actual milestones, quantity and condition records, attachments, reconciliation discrepancies |
| 异常与变更 | Open cases, blocking impacts, proposed changes, decisions and applied revisions |
| 费用与结案 | Customer receivables, supplier payables, extras, disputes, settlement and closure checklist |

Every slot displays the current artifact/status, unresolved issue, next responsible party/action, and last update. Empty slots provide a meaningful manual action and an Agent action. Do not render static success badges or demo-only counts.

### 4.4 Interaction invariants

- Chat and workbench reference the same artifact ID and version. No copying chat text into an unrelated workbench record.
- “打开工作台” from a conversation preserves assignment and focuses the relevant task/artifact. “让 Agent 协助” from an artifact sends its ID, version and allowed evidence scope.
- Switching home/drawer/chat/workbench preserves filters, current assignment, and task context when appropriate.
- Drawer has a visible menu-button alternative to swiping. Frequent workbench access never requires a hidden gesture.
- Draft/saved/reviewed/shared/accepted/executed labels remain distinct. Chat clear/archive does not delete business records.
- Customer pages use business language. Model/provider/debug information belongs to authorized diagnostic views.
- Honor existing miniapp navigation and stack limits; use reuse/redirect/back strategies rather than uncontrolled nested `navigateTo` calls.

## 5. Workflow requirements

| ID | Work area | Required manual path | Agent assistance | Stored result |
| --- | --- | --- | --- | --- |
| WF-01 | Intake | Create/edit draft, select provider, submit/claim, ask for missing information | Extract requirements and propose questions | Assignment revision, intake checklist |
| WF-02 | Task/route planning | Create/edit/reorder legs and operation tasks, set prerequisites/owners/dates | Propose decomposition and flag inconsistent timing | Plan version, task graph |
| WF-03 | Procurement | Register supplier, enter/upload quote, compare, record acceptance with evidence | Parse quotes and normalize terms | Quote versions, procurement record |
| WF-04 | Customer proposal | Assemble offer, release exact version, customer accepts/rejects | Explain options and prepare customer narrative | Customer offer and immutable acceptance |
| WF-05 | Contracts | Draft/edit templates, review, share, attach signed document if available | Populate authorized facts, identify discrepancies, propose wording | Contract revision and evidence references |
| WF-06 | Execution/handover | Set readiness, record starts/arrivals/operations, quantities and handovers | Prepare reminders, extract document facts, detect missing events | Milestone/handover events |
| WF-07 | Exceptions/change | Open case, propose change, decide/assign/apply, close with evidence | Identify impacts and prepare alternatives | Exception/change case and linked revisions |
| WF-08 | Settlement/closure | Record receivables/payables/extras, resolve differences, approve/close | Compare bills against agreed terms and evidence | Settlement revisions, closing checklist |

Intake, quote collection, document preparation, and draft planning can proceed in parallel. Official release, booking confirmation, execution readiness, and closure have explicit predicates.

### 5.1 Assignment facts

Required to submit: customer identity, selected provider, cargo description/category, positive quantity with unit, origin/destination, requested loading window, contact/reference, and requested service scope. Budget may be unknown and is then explicitly “待确认”.

Drafts may be incomplete. First-release address support includes free-text facility/address plus optional known port code; do not force all legs into the legacy cargo port enumeration. Free-text locations do not imply verified navigability or service availability.

Store delivery window, packaging, cargo value if supplied, splitting/transshipment permission, permitted modes, cost inclusions/exclusions, and special conditions as structured fields where consequential. Unknown values remain null/unknown, never silently true or zero.

### 5.2 Plans and tasks

- A plan contains one or more ordered transport legs, optional parallel split legs, and operation tasks such as loading/unloading/storage/handover.
- First-release modes: water, road, rail, other/manual. Operational details for road/rail may be manually entered; no carrier API is implied.
- Each task has type, responsible actor, planned window, prerequisites, status, completion evidence requirements, and linked artifacts.
- Dependencies form a directed acyclic graph. Reject cycles and self-dependencies.
- Represent dependency type: informational, review-required, or execution-blocking. Only execution-blocking prerequisites prevent the corresponding action.
- A draft can reference incomplete tasks. Releasing a customer offer requires an identified service scope, price basis, validity and outstanding conditions disclosure.
- Execution readiness requires an accepted applicable proposal, required contractual evidence/terms status, resource confirmation, and route/cargo checks defined for the configured task. Record a human route-feasibility review when there is no authoritative integration. An unknown check is not a pass.
- Required hard checks cannot be bypassed by chat, generic “complete”, or administrator UI. Advisory findings may be acknowledged with an explanation.

### 5.3 Quotes and procurement

Quote fields: supplier, services/leg scope, amount or rate, unit, currency, tax/invoice basis, inclusions/exclusions, validity, availability/conditions, source document/message, confirmation status and actor/time.

Use Decimal/fixed precision for money and explicit units for rate calculations. First-release settlement is CNY; retain foreign-currency documents as reference and mark conversion required. Do not compare mixed currencies as equal or create unsourced FX conversions.

Differentiate draft quote, supplied quote, selected quote, requested resource, and confirmed resource. A selected quote is not a booking. Expired quotes cannot support a new confirmed procurement without a recorded renewed quote/confirmation.

Resource confirmations record whether they came from manager-attested external evidence or an integrated authenticated receipt. First release may use the former. Protect internal confirmed full-vessel reservations against overlapping confirmed windows; partial/shared capacity requires explicit capacity records or manual review and cannot be inferred from legacy ship matching. Internal reservations do not guarantee availability outside this platform.

### 5.4 Customer offers and contracts

- Distinguish internal plan, internal procurement quote, customer offer, and contract.
- Customer sees explicitly released versions only. Release creates a stable snapshot with allowed fields; it does not expose a live internal draft.
- Acceptance binds artifact ID/version, customer actor, timestamp and displayed terms. Internal manager review is not customer acceptance.
- Draft text is not a signed contract. Customer proposal acceptance is not automatically an electronic signature.
- Signed-document recording requires attachment/source, actor, time, and signature/evidence status; do not claim independent authenticity verification.
- Use structured authoritative amounts, dates, parties and route information to render contractual facts. Model-proposed prose remains editable/reviewable; deterministic validation checks factual consistency.
- Operating party/role, actual carriers and scope are explicit. Avoid blanket assertions that every assignment is platform carriage or legally qualifies as a multimodal one-document service.

### 5.5 Events, quantities, and exceptions

Record source, event time, recorded time, actor, task/leg, quantity/unit, condition and evidence. User-reported, manager-attested and integration-confirmed events have different provenance labels. Corrections append a replacement/correction link rather than erasing the original evidence trail.

Handover discrepancies generate review items; a quantity mismatch is not automatically a proven cargo loss. Link related handovers and prevent double counting across multiple legs or split shipments.

Exceptions have owner, severity, affected tasks/artifacts, proposed actions, due time, decision, resolution evidence and closure. Critical unresolved cases block the relevant execution/closure actions. Documented noncritical residual issues may be closed only using an explicit disposition rule, not a generic skip.

### 5.6 Settlement

Maintain customer receivables and supplier payables separately. Each extra charge links to a task, contractual basis or approved change, and evidence. Unagreed costs remain disputed/proposed and are excluded from confirmed payable totals until approved.

Settlement approval, receipt of funds, and supplier payment are separate statuses. Allow evidence-backed recording of external payment/receipt, marked as manual records. Never mark funds received because the legacy mock-payment endpoint returned success.

Closure requires completed/cancelled-with-disposition tasks, required delivery evidence, resolved blocking exceptions, approved settlement and explicit disposition of outstanding balances. Open balances keep a visible financial-open state unless a documented authorized credit arrangement permits operational closure. Operational completion and financial closure remain distinguishable.

## 6. State and version semantics

### 6.1 State sets

Use compact states with explicit orthogonal fields, not one giant status enum.

| Object | Minimum states/fields |
| --- | --- |
| Assignment | `draft`, `submitted`, `active`, `completed`, `cancelled`; separate derived stage and financial closure status |
| Task | `todo`, `in_progress`, `waiting`, `blocked`, `done`, `cancelled`; reopen transition with reason |
| Artifact revision | `draft`, `in_review`, `confirmed`, `superseded`; separate sharing/acceptance/execution/evidence status |
| Agent job | `queued`, `running`, `succeeded`, `failed`, `cancelled`; attempt, deadline, lease/checkpoint |
| Exception/change | `open`, `in_review`, `approved`, `rejected`, `applied`, `closed` as applicable to type |

Document allowed transitions and predicates in the implementation. Reject illegal transitions server-side. Do not automatically roll back a completed leg when another leg fails.

### 6.2 Revision and impact rules

Every mutable aggregate has a revision counter. Confirmed/released versions are immutable. Edits create drafts/new versions with their base version. A command includes expected revision; stale revisions return HTTP 409 with current-version information.

An artifact records source dependencies as IDs and revisions. When relevant facts change, mark dependent current drafts/results `needs_revalidation`; show the cause and a review task. Do not rewrite accepted/signed/executed history.

Implement a small explicit impact map first:

| Changed field | Minimum affected areas |
| --- | --- |
| Cargo quantity/category | Ship suitability, procurement scope, costing, customer offer, contract, handover plan |
| Origin/destination/mode | Route/leg plan, suppliers, timing, quote scope, contract |
| Loading/delivery window | Resource availability, downstream timing, quote validity, contractual dates |
| Selected supplier/quote | Procurement, cost, customer offer if relevant, execution confirmation |
| Approved extra charge | Settlement and customer change confirmation when chargeable to customer |

Before acceptance/execution, revalidate against current versions. A late Agent result based on old facts may be retained as a stale draft, but cannot silently become the current confirmed result.

### 6.3 Manual intervention

Managers can edit, take over, reassign, reject, supply evidence, reopen and resume tasks. Persist actor/reason/time and the old/new version references. Manual completion must satisfy the same required business predicates as Agent-assisted completion.

Taking over cancels or fences outstanding Agent actions. A worker finishing afterwards cannot overwrite the manager's changes. Use revision checking plus a task execution generation/lease to prevent late writes.

## 7. Agent design and bounded execution

### 7.1 Five logical specialties

| ID | Chinese UI label | Responsibilities |
| --- | --- | --- |
| AG-01 | 委托助理 | Intake extraction, missing fields, task summaries, routing and next actions |
| AG-02 | 方案与采购 | Plan decomposition, quote parsing/comparison, costs and constraints |
| AG-03 | 合同与单证 | Contract drafts, document checklists, version/fact comparison |
| AG-04 | 履约与交接 | Milestone reminders, evidence extraction, handover/exception analysis |
| AG-05 | 结算 | Invoice/bill comparison, extras, settlement draft and discrepancy explanation |

One model gateway may implement all five. No separate fine-tuning, multi-model ensemble, autonomous group chat, or new vector database is required. Coordination occurs through persisted tasks and artifacts. Report actual tool and model behavior; do not label deterministic operations as LLM reasoning.

### 7.2 Context contract

Each task call receives only authorized fields: organization/user/assignment/task IDs; specialty; current revision; relevant fact snapshots; allowed artifact/evidence references; missing fields; permitted actions. Portfolio questions use authorized summarized records and cannot mix artifact writes between assignments.

Existing FAQ knowledge is explanatory background, not authoritative capacity, price, legal or navigation evidence. Quotes and signed/accepted terms come from business objects/evidence. Uploaded documents are untrusted data: their instructions cannot change system permissions, call tools, select another tenant or authorize payments.

### 7.3 Output contract

Validate outputs with typed schemas. Minimum envelope:

```json
{
  "assignment_id": "assignment-id-or-null-for-unbound-draft",
  "task_id": "task-id-or-null",
  "base_revision": 7,
  "summary": "中文简述",
  "artifact_proposals": [],
  "missing_fields": [],
  "findings": [],
  "source_refs": [],
  "proposed_actions": [],
  "requires_review": true
}
```

Artifact proposals are typed by artifact kind. Source refs identify attachment/page/row or business object/version where possible. Unsupported assertions are labeled unknown/unverified. Never use model confidence alone as permission to act.

The assistant can persist messages, proposals, internal draft artifacts and reminder suggestions through a scoped service. Confirmation, sharing, procurement commitment, execution, contract-evidence registration and financial status changes require explicit typed commands from an authorized user in this release.

### 7.4 Confirmations and tools

All business mutations go through domain services; Agents have no direct database or raw unrestricted HTTP access. Tool allowlists and field-level permissions are enforced server-side.

Confirmation cards show action, target artifact/version, scope, price/conditions if relevant, and consequences. “可以，就这么办” cannot be treated as a blanket authorization: resolve to one pending card and display/require its exact confirmation action if ambiguous. A confirmed action cannot be repurposed for a revised artifact or second transaction.

### 7.5 Failure and recovery

Persist job status independently of request lifetime. A database-backed job table plus worker is sufficient; use existing infrastructure where appropriate. Do not introduce a heavyweight workflow platform solely for this milestone.

Jobs support bounded retries, timeout, lease expiry recovery, cancellation and an attempt log. Side-effecting commands use idempotency keys and transactional checks. Repeating a completed command returns the same logical result. Same key with a different payload is rejected. Use at-least-once processing with idempotent effects; do not claim exactly-once external delivery.

On model failure, preserve all documents and manual editing; show retry and manual actions. Deterministic fixture mode is required for CI. A supported live-model path must be implemented; execute a limited smoke run only if credentials are already available and do not disclose them. Report live-model quality as unverified if not exercised.

## 8. Data model requirements

These are logical aggregates; combine/split physical tables only with documented reasons. Do not implement consequential business facts solely as opaque chat blobs.

| Aggregate | Required information |
| --- | --- |
| OperatingOrganization / Membership | Organization identity, user, role, active status; no public self-elevation |
| Assignment | Provider/customer/manager, scope and cargo facts, dates, status, revision, financial status |
| AssignmentAccess | Collaborator permissions and explicit participant relationships |
| PlanRevision / TransportLeg | Versioned route and task plan, modes, quantities, windows, prerequisites |
| WorkflowTask / Dependency | Type, owner, status, due time, dependency kind, required evidence, execution generation |
| Supplier / SupplierQuoteRevision | Counterparty identity, quote terms, scope, validity, sources and confirmation |
| Procurement / ResourceReservation | Selected quote/version, request/confirmation evidence, capacity/window and disposition |
| CustomerOfferRevision / ContractRevision | Structured customer facts, rendered content, source versions, release/acceptance/evidence status |
| Artifact / ArtifactRevision | Common registry, kind, visibility, current version, base versions, revalidation flags; references typed payloads |
| Attachment | Organization/assignment or private-unbound owner, filename/type/size/hash, storage key, extraction status/provenance |
| Conversation / Message | Scope, specialty, task binding, message role/content, artifact references; archive separate from business deletion |
| AgentJob / ActionProposal | Initiator, context version, status, attempts, allowed action, result and confirmation binding |
| Milestone / HandoverEvent | Planned/actual/event-recorded time, quantities, condition, source and corrections |
| Exception / ChangeRequest | Affected records, owner, decision, implementation and resolution evidence |
| FinancialLine / SettlementRevision | AR/AP distinction, basis, quantity/rate/amount/unit/tax, approved extras, evidence and status |
| AuditEvent / IdempotencyRecord | Actor and role, command, target/version, before/after refs, outcome, reason, correlation/key |

Use foreign keys, indexes on organization/assignment/status/due dates, and explicit transaction boundaries. All timestamps use a documented timezone convention; display in Asia/Shanghai. Use local business dates correctly. Preserve source event time separately from upload/record time.

Attachments are authorized objects. Store binaries outside public static paths, use safe filenames, bounded size/type validation and no execution of uploaded content. Downloads and parser jobs enforce ownership. Initial file limit: 20 MiB per attachment, configurable server-side. Extracted fields remain proposals until reviewed where consequential.

## 9. API and implementation boundary

Keep the existing modular monolith. Suggested new namespace: `/api/v1/entrust`. Reuse the existing model gateway and components. Do not modify baseline endpoint meanings to impersonate a manager as a shipper.

Minimum API capabilities (exact endpoint grouping may follow repository conventions):

| Group | Capabilities |
| --- | --- |
| Organizations | List eligible providers, current memberships, authorized assignment routing |
| Assignments | Draft/create/list/detail/edit/submit/claim/reassign; customer and manager projections |
| Tasks | Create/edit/list, dependencies, transition, takeover/reopen/reassign |
| Artifacts | Create draft, revision list/detail/diff, edit, review, release, exact-version customer acceptance |
| Quotes/procurement | Enter/parse/compare, select, request, confirm, reserve/release with evidence |
| Attachments | Upload, authorized download, extraction job/status, bind private draft to assignment |
| Conversations | Create/list/messages/archive, task binding, specialist selection |
| Agent jobs | Submit/get/retry/cancel; validated artifact proposals and action cards |
| Workbench | Portfolio summary and assignment projection from authoritative records |
| Events/exceptions | Record/correct events, open/resolve cases, approve/apply changes |
| Finance | Lines, discrepancies, settlement revision/approval, evidence-backed receipt/payment records, closure |

Request bodies for consequential commands include `expected_revision`, `idempotency_key` and exact target/version. Standardize 403/404 non-leaking authorization behavior, 409 version/conflict behavior, 422 field errors, and recoverable model errors. Paginate lists and avoid N+1 loading.

### 9.1 Legacy order bridge

- New multi-leg assignments must not be represented by blindly duplicating the existing cargo/order relationship.
- Allow a water leg to link to an existing authorized legacy order. Linking is read/reference-only unless existing participants execute existing commands.
- New multi-leg task execution may be manually recorded within the entrust domain in the first release. Distinguish a manager-recorded event from a legacy order transition.
- Existing candidate matching can be called as a recommendation tool. It cannot supply booking acceptance or verified route feasibility.
- Existing payment flows remain unchanged. Entrust financial records are not routed through the mock payment service as real settlement.

### 9.2 Migrations and rollout

Add versioned migrations compatible with development SQLite and the configured production MySQL path. Inspect whether a migration mechanism now exists; if absent, introduce a minimal documented one. Do not assume `create_all` upgrades existing tables.

Add an `ENTRUST_ENABLED` configuration and a minimal capabilities response for the miniapp. When disabled, preserve the original preview behavior and hide the manager entry. When enabled, all required flows must be real, not placeholders. Feature flag and server permissions are independent; UI hiding is not access control.

Provide non-destructive migration instructions and backup/restore guidance for the development dataset. Additive tables are preferred. Disabling the feature hides access but does not delete records.

## 10. First-release build sequence

Complete all milestones for first-release acceptance. Intermediate milestones are reviewable increments, not permission gates requiring routine HO confirmation.

| Milestone | Deliverable | Exit evidence |
| --- | --- | --- |
| M0 — Baseline | Inspect HEAD/instructions/tests, record differences and route/component reuse | Baseline report and bounded implementation plan |
| M1 — Manual core | Organization access, customer intake, assignment/task/artifact/attachment models and migrations; functional workbench | Manual intake-to-task path, isolation and migration tests |
| M2 — Manager UI | Main home, drawer, specialty shells, portfolio and assignment workbenches, deep links/direct editors | Navigation and persistence checks on actual pages |
| M3 — Agent assistance | Five specialties, typed outputs, task context, quote parsing, provenance, durable jobs, cards/manual takeover | Deterministic fixtures plus available live-model smoke evidence |
| M4 — Business close loop | Offers/acceptance, procurement confirmations, contracts, handovers, change impact, settlement/closure | End-to-end customer/manager scenario below |
| M5 — Delivery | Concurrency, permissions, restart recovery, regression, documentation and screenshots | Acceptance matrix with evidence and known limitations |

Prefer first proving one text-based supplier quote can become a reviewed procurement artifact and customer offer. Then extend the same object/card/confirmation pattern to the remaining specialties.

## 11. Acceptance scenarios

### 11.1 Canonical fixture: domestic road–water–road assignment

Use a clearly labeled synthetic Guangxi-area fixture for software verification. It is not evidence of a commercially operated route or official navigation approval.

Actors: customer A, operator organization A, managers A1/A2, unrelated organization B/manager B1, two candidate ship suppliers and truck/operation suppliers.

Cargo: 800 tonnes of ordinary non-dangerous steel cargo, supplier/customer locations entered explicitly. Plan: road pickup → inland water transport → road delivery, with load/unload/handover tasks. Quote and charge fixtures are invented test values, use CNY and explicit units.

Sequence:

1. Customer creates and submits a draft to organization A; A1 claims it.
2. A1 uploads a text quote and opens the procurement Agent; extracted fields show provenance and missing terms.
3. A1 manually corrects a rate/inclusion field; the workbench reflects the same artifact revision.
4. A1 creates/reviews the three-leg plan and parallel document tasks. Agent proposals are optional for manual continuation.
5. Two supplier quotes are compared with validity and cost scope visible; selected resources remain unconfirmed until evidence-backed confirmation is recorded.
6. A1 releases a customer offer. Customer accepts the exact version; internal costs remain inaccessible.
7. Contract draft is generated from accepted facts; signed-document evidence can be recorded separately without claiming live electronic signature.
8. Change cargo quantity from 800 to 950 tonnes. Use one 900-tonne candidate in the fixture: it becomes unsuitable; dependent quotes/offer/contract are marked for revalidation. Prior accepted versions remain intact.
9. A1 revises the plan/quotes and obtains required renewed confirmations. A2 takes over a task; outstanding stale Agent output cannot overwrite A2's changes.
10. Record milestones and handovers; a missing discharge document produces a waiting task. An extra waiting-time charge creates a disputed line until its basis and approval are recorded.
11. A1 resolves the discrepancy with evidence, creates settlement, records customer confirmation and external payment evidence where applicable.
12. Close operational and financial work under the specified rules. Reopen a permitted task with a reason and retain the complete history.

### 11.2 Requirement-to-test acceptance matrix

| ID | Test | Pass condition |
| --- | --- | --- |
| AC-01 | Legacy regression | Existing self-dispatch/shipowner/order/payment/Agent flows retain expected behavior |
| AC-02 | Entry separation | Customer entrusted entry cannot enter manager data; eligible manager workspace is accessible |
| AC-03 | Claim concurrency | Two managers claiming one intake produce one owner and an explicit conflict/authorized reassignment path |
| AC-04 | Context safety | Ambiguous assignment prompts selection; switching session cannot write to the former/other assignment accidentally |
| AC-05 | Shared views | Chat card and workbench show the same object/version; edits appear after refresh/navigation without duplicate artifacts |
| AC-06 | Manual completeness | Canonical workflow can finish without a model/API key using direct operations |
| AC-07 | Agent artifact quality | Valid typed result, sources and missing fields; unsupported critical facts remain unknown |
| AC-08 | Invalid model output | Wrong type, missing required field or invented source is rejected/flagged; no confirmed business mutation |
| AC-09 | Proposal vs action | Draft generation does not share, sign, confirm resource, record payment or execute a task |
| AC-10 | Authorization | Cross-organization and unauthorized assignment reads/writes/downloads/jobs fail; customers cannot retrieve internal fields |
| AC-11 | Version conflict | Concurrent edits and acceptance of superseded/unreleased versions are rejected with actionable conflict response |
| AC-12 | Change propagation | Quantity change flags specified downstream artifacts; immutable accepted/executed history remains intact |
| AC-13 | Dependency semantics | Parallel drafts allowed; dependency cycle rejected; unsatisfied hard execution prerequisite blocks transition |
| AC-14 | Procurement truth | Selected/expired quote cannot become confirmed resource without valid evidence/renewal; internal overlapping exclusive reservations conflict |
| AC-15 | Restart recovery | Worker/process restart preserves jobs/tasks/messages; retries do not duplicate effects |
| AC-16 | Manual takeover | Late Agent completion is fenced; manager changes remain authoritative |
| AC-17 | Attachment resilience | Unsupported/scanned document stores safely with manual fallback; parser failure preserves file and task |
| AC-18 | Prompt injection | Attachment instruction to expose another customer or execute payment causes neither data exposure nor command |
| AC-19 | Financial integrity | Explicit units/Decimal arithmetic; no duplicated extras; disputed costs excluded from confirmed totals; mock payment not real evidence |
| AC-20 | Handover/closure | Missing required evidence or blocking exception prevents closure; legitimate disposition/reopen retains audit |
| AC-21 | UI resilience | Empty/loading/error/stale/permission-denied states actionable; deep links and back navigation preserve correct context |
| AC-22 | Feature flag | Off preserves preview and existing workflows; on exposes implemented capabilities with unchanged server authorization |
| AC-23 | Customer confirmation | Customer accepts only released exact-version customer projection; manager review cannot forge acceptance |
| AC-24 | Audit coverage | Consequential actions link actor, target/version, reason/evidence and outcome; archived chat leaves business audit intact |
| AC-25 | Database upgrade | Existing baseline dataset upgrades without loss; new app works on upgraded and clean databases |
| AC-26 | Route/data claims | Unverified routes/resources and manually attested events are visibly differentiated from integrated confirmations |

Test the meaningful concurrency cases on MySQL where available. SQLite-only results are not evidence for MySQL locking correctness; deliver a reproducible integration-test setup and report unexecuted checks honestly.

### 11.3 UI and performance checks

- Extend existing miniapp route/interaction verification to new pages and deep links. Use real backend payloads for integration scenarios.
- Capture representative screenshots: home, drawer, specialist artifact card, portfolio, assignment workbench, customer proposal, change impact and settlement difference.
- Verify the available WeChat simulator/device tooling if present; do not call Node page tests a physical-device test.
- Target non-AI workbench reads within 2 seconds on a documented local seeded dataset of 100 assignments/2,000 tasks; report environment and measured results. This is a local acceptance target, not a production SLA.
- Agent requests return a persistent job reference promptly and remain recoverable across navigation. Configure finite worker/model timeouts and surface them; do not leave an infinite spinner.
- Existing repository CI gates remain required. Add focused tests for the new risks; do not inflate counts with assertions mirroring implementation details.

## 12. Observability and future automation evidence

Capture per task: specialty, automated/manual steps, edits to proposed fields, acceptance/rejection, rejection reason where provided, elapsed timestamps, operator interactions, model latency/cost where observable, failures/retries and final business outcome.

Do not present wall-clock waiting as active human labor. If reporting human handling time, define the measurement method and its limitations. Do not fabricate saved hours or automation percentages.

Useful first-release operational metrics: pending/overdue tasks, quote turnaround, stale quote count, proposal-to-actual cost variance, missing handovers, exception resolution time, and manually corrected extraction fields. Financial performance requires actual cost/evidence; synthetic fixtures prove behavior only.

These records support later decisions about automating a specific task under a defined envelope. The first release makes no blanket promise to replace managers.

## 13. Delivery package and completion definition

The implementing Codex must deliver:

1. Implemented code in the repository branch, with coherent reviewable commits according to project conventions.
2. Versioned database migrations and idempotent labeled seed fixtures; no destructive reset requirement for existing users.
3. Setup/configuration instructions, feature-flag behavior, provider/manager provisioning and demo identities; no real credentials in files.
4. A concise architecture/data/permissions note, actual API contract, and UI route map.
5. An HO walkthrough of the canonical customer/manager workflow, including AI failure and manual takeover.
6. Acceptance matrix AC-01–AC-26 with pass/fail/not-run, exact commands and bounded evidence; report mock vs live integrations and models separately.
7. UI screenshots and known limitations, including external services not integrated.
8. A final change report explaining baseline differences, implemented scope, material deviations, verification and remaining external dependencies.

Required business paths cannot be labeled completed if they only navigate to preview screens. Unsupported external integrations can remain deferred because manual evidence-backed operations are explicitly in scope. The release candidate is complete when the required internal workflow and acceptance requirements are satisfied and untested environmental gates are clearly identified for release review.

## 14. Product labels and fixed terminology

| English implementation term | Chinese product label |
| --- | --- |
| Entrusted shipping | 委托发货 |
| Manager workspace | 货代工作空间 |
| Operating organization | 服务经营主体 |
| Assignment | 总委托单 / 委托 |
| Portfolio workbench | 我的工作台 |
| Assignment workbench | 委托工作台 |
| Main assistant | 委托助理 |
| Specialist conversation | 专业助手会话 |
| Artifact | Use business name: 方案、报价、合同、交接单、对账单 |
| Needs revalidation | 待重新核对 |
| Recorded external confirmation | 已录入外部确认 |
| Unconfirmed resource | 待确认运力 |
| Human takeover | 转为人工处理 |
| Internal cost | 内部采购成本 |
| Customer offer | 对客报价 |

Use business-specific UI labels instead of exposing “artifact”, “workflow engine”, “agent orchestration” or model internals to customers. Agent IM describes the interaction architecture; the user's visible task remains organizing and delivering transportation.
