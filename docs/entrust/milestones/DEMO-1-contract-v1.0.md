# DEMO-1 Midterm Milestone Contract — 仓库登记本

> **本文件是登记副本。** 自下方「合同正文起始」标记之后的内容，与 HO 于 2026-09-16
> 下发的 `DEMO-1_Midterm_Milestone_Contract_v1.0.md` **逐字一致**——未作任何改写、
> 翻译、删节或重排。登记信息（本节）由开发方填写，属登记元数据，**不是合同条款**。

## 登记信息

| 项 | 值 |
| --- | --- |
| 登记日期 | 2026-09-16 |
| 登记依据 | HO 于 2026-09-16 指示：本合同构成开发中期进度上的重大更新，据此制订新的开发节奏 |
| 合同 ID / 版本 / 日期 | ENTRUST-DEMO-1 / 1.0 / 2026-09-15 |
| 合同声明基线 SHA | `e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f`（= PR #108 落地提交） |
| 登记时 `develop` | 见 `DEMO-1-plan.md` §0「基线差异」——合同基线**落后于**当前 develop，属正常，**不是回退目标** |
| 验收方 | Human Operator（HO）；合同 §13 验收记录表的正式采纳与最终结论由 HO 填写 |
| 状态 | **已登记 → 已采纳（2026-09-16）**；未声称已完成、未声称已验收 |
| 采纳日期 | 2026-09-16 |
| 采纳依据 | HO 于 2026-09-16 答复：对依本合同制订的 S0 → S5 开发节奏「同意这个安排」，并指示「下面继续执行」 |
| 采纳范围 | 采纳**本合同的执行节奏**（S0–S5 切片顺序、BP/D1 工作项映射、暂停清单边界）作为开发工作单；**不含**对本合同 §13 验收记录表的任何填写 |

⚠️ 本合同自身写明：`Document status | Proposed execution baseline, ready for HO adoption;
no completion or acceptance is asserted`。故登记动作**不等于**采纳动作，也**不产生**
任何完成度声明。§13 的验收记录表**按合同要求留空**——合同明令
`No dates or acceptance results are prefilled as if execution had occurred.`

⇒ 以上区分**依然成立**：**登记 ≠ 采纳 ≠ 完成 ≠ 验收**。本次采纳（2026-09-16）只把本合同
从「提议的执行基线」升为「开发工作单」；§13 的验收记录表**依旧留空**，未声称任何完成或验收。

## 与仓库内既有文件的关系

- 需求权威仍是 `docs/entrust/Entrusted_Shipping_Agent_IM_PRD_v1.0.md`（尤其 AC-01–AC-26）
  与 `docs/entrust/MVP开发与交付计划_船好多委托发货AgentIM_v1.7.md`。
- 本合同只变更 **DEMO-1 期间的即时里程碑目标与排期**；合同 §1.2 明令它
  **不删除既有需求、不把延期项转成已完成项、不追溯改动已采纳的业务语义**。
- 合同 §7 开篇还有一条最容易被读漏的约束：
  `"Paused" means outside DEMO-1's additional implementation obligation.
  It does not authorize deleting existing working functionality or ignoring a defect
  that breaks the accepted demo path.`

## 依据材料

- 来源文件（本机）：`C:\Users\Administrator\Downloads\DEMO-1_Midterm_Milestone_Contract_v1.0.md`
- 计划文档（同批登记）：`docs/entrust/milestones/DEMO-1-plan.md`
- R1 余量台账（同批登记）：`docs/entrust/milestones/DEMO-1-r1-remainder.md`

---

### 合同正文起始（以下逐字照录，未改动）

# DEMO-1 Midterm Milestone Contract
## Entrusted Shipping Agent IM — Runnable Business Demonstration

| Document control | Value |
| --- | --- |
| Contract ID | ENTRUST-DEMO-1 |
| Version | 1.0 |
| Date | 2026-09-15 |
| Product owner and acceptance authority | Human Operator (HO) |
| Prepared by | Codex, pursuant to HO's milestone-contract request |
| Implementing party | Development Codex / repository development team |
| Repository | mo21cn/mo21cn-pinglu-didi |
| Verified planning baseline | develop at e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f |
| Endpoint | DEMO-1: witnessed, repeatable, runnable business demonstration |
| Document status | Proposed execution baseline, ready for HO adoption; no completion or acceptance is asserted |
| Intended repository location | docs/entrust/milestones/DEMO-1-contract-v1.0.md |
| Nature | Engineering scope, implementation, and acceptance agreement; no payment terms or legal-service agreement |

> **Execution decision:** Deliver one genuine customer-to-manager business lifecycle on the existing application, with persistent business records, one working quotation Agent conversation, human intervention, and demonstrable settlement and closure. Use a controlled demonstration environment and prepared accounts. Defer public-service readiness and expansion outside this lifecycle. Preserve the interfaces and requirement ledger needed to finish the agreed R1 scope later.

### 中文执行摘要

本合同把 DEMO-1 定义为正式中期验收点：甲方能够通过真实界面操作，看到“客户委托—经理认领—报价助手—对客确认—合同与采购—履约交接—异常变更—结算结案”在同一批真实数据库记录上连续运行。演示使用明确标记的合成业务数据，不宣称已完成真实航运、采购、签约或支付。

五个业务包为：BP-01 客户入口与受理；BP-02 报价 Agent IM；BP-03 对客方案、采购确认与合同；BP-04 履约、变更、结算与结案；BP-05 演示环境、数据复位与验收证据。它们是交付组织方式，不是五个独立系统，也不是五个 Agent。

可以暂停面向公众的注册运营、额外专业 Agent、外部业务系统接入及非关键扩展；不能省略客户精确版本确认、服务端权限、持久化、人工接管、金额正确性、证据来源、真实结案条件和既有 CI。五个包全部符合本合同验收条件，才可提交 DEMO-1 验收；DEMO-1 通过不等于 R1 全部通过，也不等于允许面向公众上线。

本次形成的是可审阅、可直接作为开发工作单采用的完整合同文件，不表示已修改仓库、批准 PR 合并或完成里程碑验收。

## 1. Purpose, authority, and scope precedence

### 1.1 Outcome

A customer representative and a human forwarding manager shall operate the implemented product and produce inspectable, consistent business records for one domestic road–water–road assignment. The manager shall be able to continue manually at every required business step, including when the model is unavailable.

The demonstration shall prove that the system organizes work and preserves business truth. It shall not depend on developers editing database rows, invoking hidden administrative actions, or replacing business actions with screenshots during the acceptance walkthrough.

### 1.2 Relationship to the master requirements

The following remain authoritative for requirements and implementation safeguards:

1. Entrusted_Shipping_Agent_IM_PRD_v1.0.md, especially the canonical scenario and AC-01–AC-26.
2. The adopted delivery plan and subsequent HO decisions.
3. Repository AGENTS.md and adopted decision records, including DR-0003, DR-0009, DR-0010–DR-0017 where relevant.
4. This contract, for the explicitly bounded DEMO-1 scope and sequencing after adoption.

This contract changes the immediate milestone target. It does not erase master requirements, convert deferred work into completed work, or reopen adopted business semantics by implication. Its explicit deferrals take precedence over previous scheduling for DEMO-1 only. Identity, authorization, versioning, truthfulness, and migration safeguards remain in force.

A stale progress sentence in an older plan is not evidence that implemented work must be repeated. Record the current code/evidence and correct that sentence. A passed component test is not evidence that its complete business requirement is accepted.

### 1.3 Decisions fixed by this contract

| Decision | Binding DEMO-1 direction |
| --- | --- |
| Repository strategy | Continue the existing repository and module; no new application or permanent parallel branch |
| Product workflow | Human manager organizes the lifecycle; Agent proposes and assists |
| Acceptance audience | HO and the commissioning party, using prepared test identities |
| Business breadth | One ordinary domestic road–water–road scenario, with one consequential quantity change and one charge dispute |
| AI breadth | One complete AG-02 quotation conversation; preserve existing AG-01 capability without expanding it for this milestone |
| Core operation | All mandatory business steps must also work without a model key |
| AI evidence | One separately recorded live quotation-model run is required for the complete AI demonstration; fixture runs remain the default in CI |
| External operations | Manual evidence-backed records; no requirement for live booking, electronic signature, payment, customs, AIS, or carrier integration |
| Closure | Implement the actual scoped business closure service and its prerequisites; a shortcut checking only exceptions is prohibited |
| Engineering gates | Keep existing required CI and repository controls |
| End condition | All mandatory DEMO-1 checks pass and HO records acceptance; R1 and production-release decisions remain separate |

A live-model credential problem blocks the live-AI acceptance item, not manual development or other packages. A fully working manual/fixture preview may be shown, but shall be reported as a partial preview until the live item passes or HO explicitly revises this contract.

### 1.4 Implementation authority

After this contract is adopted as the work order, the developer may make routine, reversible implementation choices within scope: file organization, focused tests, additive migrations, endpoint details consistent with existing conventions, and small PR boundaries.

Do not send each ordinary coding choice back to HO. Escalate only a concrete conflict with adopted scope or semantics, a necessary external resource/spend decision, or a consequential action that lacks existing authorization.

DR-0003 remains applicable: preparing a PR is not authorization to merge it. HO authorization is required under the existing repository policy; an AI author cannot count its own review as independent approval. This contract does not authorize production deployment, external business commitments, or real funds movement.

## 2. Baseline and implementation approach

### 2.1 Planning snapshot

The branch baseline was verified at commit e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f. This is a planning reference, not a requirement to revert later work.

The repository already contains substantial organization/assignment, task, artifact/revision, exception/change, authorization, migration, and verification infrastructure. Existing session/job services and the UI-03 artifact-card screen provide reuse points. Their existence shall not be reported as proof of a finished conversational business workflow.

The adopted A2 boundary assigns DR-0013 validations 17–19 to A2; validations 10 and 20 belong to formal S3 closure. Because BP-04 introduces that closure, validations 10 and 20 become mandatory in DEMO-1. This does not retroactively change A2's accepted boundary.

### 2.2 Reuse rules

- Preserve the FastAPI/Python/SQLAlchemy and native WeChat miniapp stack.
- Place entrusted business code in the existing entrusted module and registered page structure.
- Extend the existing session/job/model gateway and artifact mechanisms.
- Use existing versioned migration infrastructure. Append migrations when data semantics require them; do not restore a blanket “no migration” restriction.
- Preserve stable assignment/task/artifact/case identities. Organization membership and assignment authorization remain distinct.
- Use shared domain services for manual actions and Agent adoption. Do not build a parallel demo-only write path.
- Use the feature flag to control exposure. A disabled capability must not bypass backend authorization or silently fall through to legacy behavior.
- Do not introduce a new message bus, workflow engine, Agent framework, or database solely to complete this milestone.

## 3. Scenario and evidence boundary

### 3.1 Canonical fixture

Use the existing PRD scenario:

| Element | DEMO-1 fixture |
| --- | --- |
| Geography | Clearly labeled synthetic Guangxi-area road–water–road assignment |
| Cargo | Ordinary, non-dangerous steel cargo |
| Initial quantity | 800 tonnes |
| Changed quantity | 950 tonnes |
| Constraint example | A 900-tonne candidate becomes unsuitable after the change |
| Legs | Road pickup, inland water transport, road delivery, with loading/unloading/handover tasks |
| Parties | Customer A; organization A; managers A1 and A2; unrelated organization B/manager B1 |
| Commercial data | Invented CNY quotations with explicit units, validity, inclusions, and evidence |
| Exception | Missing discharge/handover evidence |
| Financial discrepancy | One waiting-time extra initially disputed |
| Data label | Synthetic scenario / manually recorded evidence, as applicable |

The fixture demonstrates software behavior. It is not evidence that a Guangxi route is commercially operated, that navigation permission exists, or that a supplier has committed capacity.

### 3.2 Required truth distinctions

The interface, stored records, and acceptance report must distinguish:

| Recorded state | Must not be represented as |
| --- | --- |
| Quotation selected | Supplier/resource confirmed |
| Internal review or artifact confirmation | Customer acceptance |
| Contract draft / uploaded sample signature evidence | Live electronic signature |
| Model proposal | Authorized business execution |
| Manual supplier/event attestation | Integrated carrier/port confirmation |
| Mock payment or sample receipt | Actual receipt of money |
| Seeded historic record | A record created through this acceptance run |
| Deterministic model fixture | Live model invocation |
| Read-only preview | Completed business action |

### 3.3 Environment

Use an isolated test/demo environment, existing supported database configuration, preprovisioned accounts, and the ordinary application authentication and permission path.

Prepared identities may avoid public registration and onboarding work. They may not allow an arbitrary client-supplied organization or role to become authorization. Use separate customer and manager accounts rather than repeatedly changing one account's role to stage the demonstration.

Record the runtime, application commit, database migration state, model mode, and device/simulator used. No production-readiness claim follows from availability of this environment.

## 4. Five business packages

Each package shall deliver a runnable vertical increment: user action, backend rule, persistent result, and evidence. The implementing team may split it into several small PRs without seeking a new scope decision.

### BP-01 — Customer intake and manager acceptance

**Business result:** A customer submits a real assignment and an authorized manager takes responsibility for it.

**Required implementation**

1. Connect the entrusted-shipping choice in the actual publishing flow to the implemented customer intake when the feature is enabled.
2. Provide a minimal UI-07 draft form with cargo, quantity/unit, origin/destination, time requirements, operator organization, and supporting notes/attachments where relevant.
3. Save drafts and submit them through the backend; show validation and explicit failure states.
4. Show the submitted assignment in the organization's real intake queue.
5. Allow A1 to claim it, with the existing single-owner/concurrency rules.
6. Show the customer its real status and assigned service organization/manager as permitted.
7. Carry the exact assignment context into the manager workbench and subsequent conversation.

**Persisted outputs:** Assignment ID, submitting customer, organization, declared facts, submission/claim state, responsible manager, actor/time audit.

**Package boundary:** Prepared accounts and organizations are sufficient. Public acquisition, registration, provider discovery, and general onboarding are deferred.

**Exit evidence:** A fresh UI-created assignment survives reload, appears in the correct queue, can be claimed once, and remains inaccessible to unrelated organization B.

**Primary acceptance:** D1-01, D1-02, D1-11, D1-14.

### BP-02 — Quotation Agent IM and manual adoption

**Business result:** A manager uploads a quote, asks the procurement Agent to extract/interpret it, corrects the result, and uses the same versioned artifact from chat and workbench.

**Required implementation**

1. Extend the entrusted UI-03 session screen with actual message input, message history, attachment selection, and job progress.
2. Bind every session/run to organization, assignment, actor, allowed actions, and the relevant input artifact/version.
3. Run one quotation task through the existing job/model gateway.
4. Render a typed proposal with price/unit, validity, inclusions/exclusions, sources, and explicitly unknown terms.
5. Provide edit, adopt, retry, and manual-continuation actions as supported by real backend capability.
6. Adoption must call authorized domain services; it must not release an offer, accept on behalf of a customer, confirm a supplier, or execute a task.
7. Display the identical artifact ID/revision in chat, artifact detail, and workbench.
8. Preserve messages and job state across navigation; apply finite timeouts and visible failure handling.
9. Fence late/stale Agent output after manual edit or takeover.
10. Demonstrate one real model invocation separately from deterministic CI fixtures.

**Persisted outputs:** Session/message IDs, job/run state, input references, model-mode evidence, proposal, artifact revisions, adoption/takeover audit.

**Package boundary:** AG-02 is the single fully demonstrated specialty. Do not add AG-03/04/05 or redesign AG-01 to satisfy DEMO-1. No autonomous procurement or payment actions.

**Exit evidence:** One live quotation run plus an offline/manual continuation. A correction appears in all shared views; stale output cannot overwrite it. No hidden provider credential is exposed in UI or evidence.

**Primary acceptance:** D1-03, D1-04, D1-05, D1-11, D1-12.

### BP-03 — Customer offer, supplier confirmation, and contract

**Business result:** Internal planning becomes a controlled customer commitment, supported by procurement and contract records.

**Required implementation**

1. Create/edit the three-leg plan and required parallel document/operation tasks using existing task conventions.
2. Compare two supplier quotations, with capacity, price scope, quantity unit, validity, and evidence visible.
3. Record selection separately from resource confirmation. Confirmation requires an authorized action and identified evidence; expired or unsuitable resources cannot be confirmed without legitimate renewal/change.
4. Prepare a customer offer using a server-side customer whitelist projection.
5. Release a specific immutable offer revision to the customer.
6. Let the authenticated customer view and accept or reject that released revision; retain actor, time, target revision, and response.
7. Reject acceptance of an unreleased or superseded offer. Internal manager review must not fabricate customer acceptance.
8. Generate one structured contract draft/template from the accepted facts and approved scope.
9. Permit upload/recording of signature evidence with source and mode labels. Retain previous contract/offer versions.
10. Permit document viewing or download needed for the demonstrated customer journey without exposing internal costs, conversations, or protected attachments.

**Persisted outputs:** Plan/leg/task references, supplier quote and confirmation records, released offer revision, customer response, contract revision, evidence references.

**Package boundary:** One domestic contract template is sufficient. Manual evidence recording is sufficient. Live e-signature, supplier-platform integration, OCR expansion, and a general document-template designer are deferred.

**Exit evidence:** Customer acceptance targets the exact released version. The customer cannot retrieve internal data via API/download. A chosen quotation alone does not create confirmed capacity.

**Primary acceptance:** D1-06, D1-07, D1-08, D1-11, D1-15.

### BP-04 — Execution, change, settlement, and genuine closure

**Business result:** A manager can complete the scoped transport lifecycle, respond to a real change, reconcile costs, and close only when the required conditions are satisfied.

**Required implementation**

1. Record planned/actual milestones and loading, discharge, and handover evidence against stable tasks/legs.
2. Store business-event time separately from recording time, with actor and source.
3. Raise a quantity-change case from 800 to 950 tonnes and apply the existing structured approval/application flow.
4. Reuse the adopted DR-0016 task/artifact impact mapping. Do not invent a “handover artifact” when handover is represented by a task.
5. Mark affected records for revalidation, produce actionable review tasks, and retain accepted/executed history.
6. Make the 900-tonne candidate's unsuitability a deterministic capacity check; an LLM opinion is not the rule.
7. Allow A2 to take over a task, complete the legitimate corrections, and obtain renewed approvals/acceptances where the changed customer commitment requires them.
8. Record missing discharge evidence as a waiting/blocking condition with an actionable remedy.
9. Record receivable/payable lines and one extra waiting-time charge with quantity, unit, Decimal amount, currency, counterparty, basis, and status.
10. Keep disputed charges out of confirmed totals; approve or resolve a charge through an explicit action with evidence.
11. Prepare a settlement draft, obtain required internal/customer confirmation, and record external-payment evidence where applicable, visibly marked as sample/manual in the demo.
12. Implement operational and financial closure with the prerequisites in Section 6.4.
13. Support the existing permitted task/case reopening semantics, with reason and history; re-evaluate affected readiness.

**Persisted outputs:** Events/handover evidence, case/impact links, approval snapshots, applied revisions, revalidation tasks, charge lines, settlement/confirmation records, closure/reopen audit.

**Package boundary:** One controlled change and discrepancy walkthrough is sufficient. Preserve existing mapping coverage and regression gates. No new universal dependency graph, automatic route optimizer, or bank connection is required.

**Exit evidence:** The scenario reaches legitimate closure; missing evidence, a blocking case, and unresolved financial conditions each prevent the applicable closure. DR-0013 validations 10 and 20 are connected to the actual closure command and tested.

**Primary acceptance:** D1-09, D1-10, D1-12, D1-13, D1-15.

### BP-05 — Demonstration environment, reset, and acceptance package

**Business result:** HO and the commissioning party can repeatedly operate and inspect the delivered lifecycle without developer-only business shortcuts.

**Required implementation**

1. Provide a reproducible build/start/migration/seed procedure for the supported demo environment.
2. Prepare identities, organizations, suppliers, quote/evidence samples, and model configuration.
3. Provide three fixture states: clean start, change/revalidation checkpoint, and completed historical assignment.
4. Run the principal acceptance from a fresh UI-created assignment. Checkpoint/history fixtures support explanation and recovery, not substitution for unexecuted actions.
5. Provide an isolated reset procedure that restores coherent database and attachment state for the matching application/migration version.
6. Make reset unavailable to ordinary product users and unable to target production accidentally. Prefer a documented environment-specific administrative script over a product reset API.
7. Deliver a walkthrough script with roles, actions, expected visible results, evidence references, and truthful fallback labels.
8. Record one witnessed complete run and the mandatory negative/recovery checks.
9. Package evidence, limitations, changed requirements, and the R1 remainder ledger.
10. Allow the witness to vary at least one supported quantity/rate/input and observe the resulting recalculation or validation.

**Package boundary:** A stable controlled environment is sufficient; public registration, broad device certification, HA/on-call operations, and production SLA are deferred.

**Exit evidence:** The environment starts from documented instructions, the run is repeatable, UI actions persist, and outputs can be inspected by ID/revision. A recording alone is insufficient if the witness cannot operate the delivered system.

**Primary acceptance:** D1-01–D1-18, with package-specific evidence reused rather than duplicated.

## 5. UI and workbench contract

### 5.1 Eight interface roles

Retain the established information architecture. UI identifiers are product roles, not a requirement to create eight new route files.

| UI | DEMO-1 content | Package owner |
| --- | --- | --- |
| UI-01 Manager home | Current organization/assignment, actionable intake/work summary, direct workbench/session entry | BP-01/05 |
| UI-02 Drawer | Assignment selection, implemented Agent entries, recent sessions, workbench, return to existing app | BP-02/05 |
| UI-03 Agent IM | Actual quotation conversation, attachment context, job states, proposal cards, adoption/manual action | BP-02 |
| UI-04 Portfolio | Existing organization-scoped assignment and exception lists with exact clickable targets | BP-01/04 |
| UI-05 Assignment workbench | Seven live slots derived from authoritative records; direct manual actions | BP-01/03/04 |
| UI-06 Artifact detail/editor | Typed fields, source, revision, permitted review/release/response/evidence actions | BP-02/03/04 |
| UI-07 Customer intake/detail | Submit, released offer/contract, exact-version response, progress, customer settlement | BP-01/03/04 |
| UI-08 Exception/change detail | Cause, affected objects, approval snapshot, apply, revalidation, disposition/history | BP-04 |

Do not rebuild the existing read-only global customer-service page as a second entrusted domain. Reuse the dedicated entrusted session route and shared components where appropriate.

### 5.2 Seven business slots

| Slot | Required real records/actions in DEMO-1 |
| --- | --- |
| Assignment overview / 委托概况 | Submitted facts, quantity/unit, parties, ownership, missing information |
| Plan and tasks / 方案与任务 | Three legs, loading/unloading/handover/document tasks, prerequisites |
| Procurement and quotations / 采购与报价 | Two quotations, comparison, selection, evidence-backed resource confirmation |
| Customer offer and contract / 对客方案与合同 | Release, exact-version customer response, contract/evidence status |
| Execution and handover / 履约与交接 | Events, actual quantities, proof, missing-evidence task |
| Exceptions and changes / 异常与变更 | Real case references, blocking state, approved/applied changes and revalidation |
| Costs and closure / 费用与结案 | Charge lines, dispute, settlement, confirmations, closure checklist |

Reuse adopted machine slot keys and registry definitions. The table names business meaning; it does not authorize renaming stable keys.

Every slot must show a truthful empty/pending/ready state, next action, responsible role, and access to its underlying record. Do not fill an unimplemented slot with a decorative “completed” badge.

### 5.3 Navigation and non-linear work

A manager may enter through chat or workbench and resume any authorized task. The workflow is non-linear for preparation; execution remains subject to explicit prerequisites.

Chat-to-workbench and workbench-to-chat links preserve assignment and exact target context. Drawer gestures have a visible button alternative. Register actual routes and transitions under DR-0011; use the existing reuse/replace/back strategy.

The demonstrated path must work through real entrances and return navigation. URL injection alone does not prove entrance reachability. Do not redesign the full navigation shell for cosmetic consistency.

## 6. Non-negotiable business and technical rules

### 6.1 Identity, authorization, and data

1. Check organization membership, assignment authority, action capability, and current business state on the server.
2. Derive client actions from backend capabilities; the client is not the permission authority.
3. Keep customer projections server-side. Check attachment access as well as JSON response fields.
4. Ensure artifacts, tasks, cases, evidence, jobs, and monetary lines are attributable to the exact assignment.
5. Use stable IDs and real persisted records. Do not compute portfolio cases by client-side fetching every assignment.
6. Treat unknown quantities, terms, and evidence as unknown. Do not insert zero/empty placeholders to force a completed state.

### 6.2 Version, action, and job safety

1. Material edits create or reference the appropriate new revision.
2. Customer response and approval/application bind exact versions or structured snapshots.
3. Writes use the existing idempotency mechanism; retries cannot duplicate confirmation, charge, event application, or closure.
4. Stale revisions and stale execution generations produce explicit conflicts.
5. Human takeover fences late Agent output.
6. Jobs persist recoverable state. A process restart cannot leave a job falsely completed or permit a duplicated business effect.
7. Agent output is validated for type, facts, and source scope before adoption.
8. Uploaded instructions cannot expand data access or authorize business commands.

### 6.3 Business fact and financial integrity

Use Decimal/fixed precision, explicit quantity units, CNY for the selected fixture, and documented rounding rules. Differentiate supplier cost from customer price.

Each charge has its own identity and basis. Retries and case resolution cannot add the same charge twice. Disputed lines remain visible and excluded from confirmed settlement totals until legitimately resolved.

Do not let a sample payment record masquerade as real payment evidence. The controlled demo may exercise a fully settled state using labeled synthetic evidence; this proves the state machine and audit, not movement of funds.

### 6.4 Closure: required implementation semantics

Closure must be a real domain command with server-enforced prerequisites and reasoned denial responses. Reuse the existing business state vocabulary; if an additive status/field is necessary, document and migrate it.

| Closure dimension | Minimum required predicate |
| --- | --- |
| Task disposition | Required execution and review tasks are completed or legitimately disposed of under allowed rules |
| Evidence | Required loading/discharge/handover evidence is present and attributable to this assignment |
| Exceptions | No unresolved blocking case or unresolved blocking revalidation remains |
| Settlement | Required settlement approval and customer confirmation are complete for the applicable version |
| Balance/disputes | Required balances are cleared under the recorded evidence mode; unresolved disputed amounts prevent financial closure |

Operational completion may be recorded before financial completion if the adopted state model permits it. The interface must distinguish the two. “Assignment closed” must not imply both have passed when only operational work is complete.

The actual final-close command must evaluate all applicable predicates atomically with its state transition. Close-versus-new-blocking-case concurrency must be safe on MySQL. An exception query existing in the code is not this proof.

Reopening requires a reason, authorization, and retained history. A newly reopened prerequisite must invalidate or re-evaluate the applicable readiness/completion state. Never erase the prior closure event.

### 6.5 Engineering safeguards

- Keep existing mandatory CI, including MySQL migration/concurrency checks where defined.
- Add focused tests for new authorization, release/acceptance, monetary, and closure transitions.
- Preserve legacy interface meaning and feature-flag behavior.
- Add versioned migrations for schema changes; test clean and upgraded databases.
- Do not disable checks, remove failing tests, or loosen assertions to accelerate delivery.
- Do not rewrite shared Git history or move released tags.
- Keep errors observable and user actions recoverable; no silent exception swallowing.

## 7. Explicit pauses and re-entry conditions

“Paused” means outside DEMO-1's additional implementation obligation. It does not authorize deleting existing working functionality or ignoring a defect that breaks the accepted demo path.

| Pause ID | Paused work | DEMO-1 substitute | Re-entry trigger / destination |
| --- | --- | --- | --- |
| P-01 | Public registration, broad customer/organization onboarding and provider discovery | Prepared accounts, organizations, authorization | Next R1 service-readiness contract defines real-user scope |
| P-02 | Live payment, electronic signature, booking, carrier/port/customs integrations | Labeled manual/sample evidence through real business services | Explicit integration work order with provider access and acceptance criteria |
| P-03 | AG-03/04/05 and expansion of autonomous multi-Agent orchestration | Complete manual domain operations; one AG-02 conversation | Later Agent scope contract; not automatically part of current R1 |
| P-04 | Additional AG-01 interaction breadth beyond existing capability | Manual intake plus retained AG-01 behavior | Remaining adopted two-Agent R1 acceptance |
| P-05 | Universal dependency DAG, visual dependency editor, cross-assignment impact graph | Existing fixed prerequisites and adopted impact mapping | Explicit broader workflow requirement |
| P-06 | Route optimization, ETA prediction, live AIS/weather/lock scheduling | Synthetic declared route and manual event records | Validated data source plus separate feature contract |
| P-07 | General contract designer, advanced export suite, expanded OCR/parser support | One usable template, existing extraction and manual fallback | Demonstrated customer need or remainder requirement |
| P-08 | Broad portfolio analytics, arbitrary multi-currency/tax/accounting integrations | Selected fixture, real charge lines, scoped settlement | Next contract specifies required business breadth |
| P-09 | New scale/load program and PRD 100-assignment/2,000-task performance target | Responsive documented demo dataset; no hangs/timeouts in required flow | R1 performance evidence; do not relabel demo timing as that benchmark |
| P-10 | Production HA, public operations, extensive device matrix, SLA/on-call | Controlled stable environment and a supported witnessed runtime | Real-user release/readiness contract |
| P-11 | IDE startup root-cause campaign and wholesale tooling replacement | Adopted read-only probe, reuse, bounded owned-instance recovery | Reproducible issue preventing the selected runtime after bounded recovery |
| P-12 | Unrelated legacy TODO expansion and non-blocking cosmetic/type debt | Preserve existing regression gates; repair actual affected regressions | Prioritized legacy/debt work order |
| P-13 | Repeated exhaustive manual runs for every small PR | Risk-targeted PR verification plus one integrated candidate run | Change impact warrants a specific additional test |

P-02 includes mock-versus-real payment reporting from existing decisions: verification of a post-payment screen does not prove a native payment-confirmation click or real funds receipt.

P-11 does not waive runtime acceptance. If the usual IDE is blocked, another supported device/simulator may supply separately labeled runtime evidence; the blocked route remains blocked. Static/Node tests do not become physical-device evidence.

A paused item that becomes necessary for a mandatory acceptance condition shall be reported as a concrete dependency, not silently implemented in full or silently waived.

## 8. Delivery sequence and scope control

### 8.1 Execution slices

| Slice | Work | Exit |
| --- | --- | --- |
| S0 — Lock the execution baseline | Register this contract, source SHA, scope delta, acceptance IDs, remainder ledger, and package estimates | No unresolved ambiguity about required demo actions or closure rules |
| S1 — Customer-to-manager path | BP-01 plus minimal BP-05 accounts/environment | Fresh submit/claim/workbench path runnable |
| S2 — Quote conversation | BP-02; retain manual fallback | Typed quotation proposal, manual correction, shared version, job recovery |
| S3 — Commercial commitment | BP-03 | Supplier confirmation, released offer, customer exact-version response, contract record |
| S4 — Execution to closure | BP-04 using existing A1/A2 infrastructure | Change, revalidation, handover, dispute, settlement and real closure |
| S5 — Integrated candidate | BP-05 evidence, reset rehearsal, remaining negatives | All mandatory D1 checks reported; candidate ready for HO acceptance |

Start BP-05 environment and fixtures during S0/S1. Do not defer integration to the final slice. Components may be developed independently behind stable service contracts, but demonstrate a working increment after each slice.

### 8.2 First implementation deliverables

The developer shall produce, without waiting for additional product decisions on ordinary implementation details:

1. A baseline delta table: reusable implementation, missing connection, missing domain behavior, and evidence gap.
2. Small repository-native work items mapped to BP and D1 IDs.
3. An estimate range for each package and the dependency path, distinguishing developer effort from external waiting.
4. Necessary API/schema changes and migrations, with preserved field/state semantics.
5. The R1 remainder ledger specified in Section 11.

Do not make a new percentage-complete promise by counting PRs, tests, endpoints, or pages. Report which business exits are demonstrable. No calendar deadline or payment amount is invented by this contract.

### 8.3 PR and integration rules

Use existing feature branches from current develop and the repository's PR format. A PR description must identify:

- BP/D1 requirements affected;
- visible result and domain-rule changes;
- migrations/API/customer-projection impact;
- executed evidence and explicitly unexecuted checks;
- rollback/feature exposure and remaining limitations.

Keep feature implementation separate from broad formatting/dependency changes. Use an early draft PR when useful. Current required CI must be green on the reviewed candidate; later changes require affected evidence to be refreshed.

Do not make one huge five-package PR merely to reduce review steps. Do not create a new mandatory HO checkpoint for every internal task. Existing merge authorization remains the only PR approval policy unless HO changes it explicitly.

### 8.4 Change control

A change needs an explicit contract amendment only if it changes a mandatory business outcome, accepts a formerly prohibited shortcut, adds a material external commitment, or alters an adopted cross-cutting rule.

For a genuine blocker, present: blocked D1 ID, observed evidence, smallest viable alternatives, recommended choice, and impact on the next contract. Continue independent work.

Scope additions discovered during development go to the remainder ledger unless essential to this contract's mandatory exits. Removing a live-AI or closure criterion requires a visible amendment; it cannot be relabeled as an implementation choice.

## 9. DEMO-1 acceptance matrix

Every D1 row is mandatory. “Required evidence” means evidence produced by the implementing team for the acceptance candidate, not evidence asserted by this document.

| ID | Observable acceptance result | Required evidence |
| --- | --- | --- |
| D1-01 | Prepared environment starts and a customer creates/submits a fresh assignment from the real entry | Version/config manifest, UI run, assignment ID |
| D1-02 | A1 claims the assignment; competing claim cannot create a second owner | UI/API result and existing or extended concurrency test |
| D1-03 | One live AG-02 quotation conversation produces a typed, sourced proposal with visible job state | Redacted live invocation evidence, job/message/artifact IDs, visible result |
| D1-04 | Manual correction/adoption is reflected as the same artifact/revision in chat, detail and workbench | Cross-view record comparison before/after |
| D1-05 | The entire scoped business lifecycle can continue without a model/API key | Offline/manual walkthrough evidence; no blocked required action |
| D1-06 | Two quotes can be compared and a valid resource confirmed only with proper evidence | Validity/capacity/evidence positive and negative cases |
| D1-07 | Customer accepts/rejects an exact released offer revision; stale/unreleased response and manager impersonation fail | Customer UI plus API negatives and revision audit |
| D1-08 | Contract draft derives from accepted facts; evidence and status remain distinct from live e-signature | Contract version and linked evidence inspection |
| D1-09 | 800→950 change follows approve/apply/revalidate flow and makes the 900-tonne candidate unsuitable | Case snapshot, mapped targets, preserved old revisions, refreshed confirmation |
| D1-10 | Handover evidence, disputed extra, settlement and confirmations are recorded; totals are correct | Event/evidence IDs, Decimal calculations, charge/settlement versions |
| D1-11 | Unauthorized assignment/org/customer reads/writes/downloads/jobs fail without data leakage | Backend negative tests, customer projection inspection |
| D1-12 | Takeover/restart/retry cannot overwrite human work or duplicate consequential effects | Delayed-job test, persisted restart/recovery result, idempotency evidence |
| D1-13 | Formal closure succeeds only with all applicable prerequisites; missing evidence/blocking case/financial gap is rejected | Real command/UI, reasoned refusals, DR-0013 tests 10/20, MySQL race evidence |
| D1-14 | Home/drawer/session/workbench/customer/case navigation works through real entrances and preserves context | Witnessed runtime navigation/back/re-entry, supported stack evidence |
| D1-15 | One supported value is changed by the witness; downstream calculation/validation changes accordingly and survives reload | Before/after records and UI result; no hardcoded replay |
| D1-16 | Existing required CI is green and applicable new migrations work on clean and upgraded databases | Candidate SHA, CI links, migration and regression reports |
| D1-17 | Environment can be reset coherently; fresh-run and seeded-checkpoint evidence are distinguishable | Reset instructions/rehearsal and labeled fixture manifest |
| D1-18 | Final evidence package and original-requirement remainder ledger are complete and internally consistent | Files in Section 12, HO acceptance record after review |

For D1-03, deterministic fixtures do not satisfy the live invocation item. For D1-05, a live run does not prove offline/manual completeness. The two requirements intentionally test different properties.

For D1-12 and D1-13, API/service and MySQL tests may prove race conditions more reliably than a staged UI performance. The user-visible entry and successful/denied result must still be demonstrated.

## 10. Walkthrough and evidence rules

### 10.1 Witnessed business script

Target a concise 15–20 minute main presentation after setup; this is a presentation budget, not a software latency or development-duration promise. Explain accelerated synthetic business time. Do not wait for a real voyage.

1. Customer submits a new assignment; A1 claims it.
2. A1 uploads the sample quotation and invokes AG-02.
3. A1 corrects one field; open the same artifact from the workbench.
4. Show the road–water–road plan and required task prerequisites.
5. Compare two quotations and record evidence-backed procurement confirmation.
6. Release the offer; customer accepts its exact revision.
7. Create the contract and record labeled sample signature evidence.
8. Apply the 800→950 change; inspect affected records and unsuitable capacity.
9. Complete required revalidation/renewed acceptance; A2 takes over a task.
10. Record handovers; demonstrate missing evidence and a disputed extra.
11. Resolve them with evidence and confirm settlement.
12. Close under the actual rules; inspect retained history and a permitted reopen/re-evaluation.
13. Reload/re-enter and inspect the same persistent records.

The witness may pause and inspect IDs, revisions, evidence, and changed values. Backup recordings/checkpoints are explanation aids and do not replace missing mandatory execution evidence.

### 10.2 Focused negative demonstrations

At minimum, retain evidence of these failures:

- unauthorized customer/org access;
- stale offer acceptance or late Agent adoption;
- duplicate monetary/confirmation request;
- unsuitable/expired resource confirmation;
- closure with missing proof or a blocking case;
- financial closure with unresolved dispute/balance;
- model unavailable or invalid output, followed by manual continuation.

Not every negative must consume presentation time. Automated/API evidence may accompany the main UI run, provided its scope and candidate version are explicit.

### 10.3 Result classification

Follow the adopted reporting semantics:

| Result | Meaning | Counts as passed? |
| --- | --- | --- |
| PASS | Required behavior observed with adequate evidence | Yes |
| FAIL | Behavior violates expected outcome | No |
| ENV_BLOCKED | Required run could not execute because of environment | No |
| REVIEW_REQUIRED | Evidence is incomplete/ambiguous and needs review | No |
| NOT_RUN | The check was not executed | No |
| LIMITATION | The method did not cover the claimed behavior | No |

Preserve original failed attempts and link reruns. Record console evidence from the current run; apply only adopted phase-bound noise exemptions. Missing logs are not proof of no errors.

Only completed tests with zero failures/errors and correctly reported skips may support the relevant check; an incomplete or untrustworthy report does not become PASS because an exit-code workaround exists. Keep process errors and their interpretation visible.

### 10.4 Candidate decision

DEMO-1 is ready for acceptance only when all D1 rows pass, the required CI is green for the candidate, and no unresolved defect prevents the witnessed lifecycle or violates Section 6.

HO records the final acceptance verdict and any observations. Non-blocking issues may remain only when they do not invalidate a mandatory D1 result and are explicitly carried forward.

A demonstration with blocked live AI, broken closure, missing customer confirmation, or unproven authorization is a partial preview. It is not “DEMO-1 accepted with those mandatory items assumed.”

## 11. Interfaces to subsequent milestone contracts

### 11.1 Preserve these contracts now; do not build speculative integrations

| Interface | Preserve in DEMO-1 | Later extension |
| --- | --- | --- |
| Identity and delegation | Organization, actor, customer, assignment authorization and backend capability evaluation | Real-user onboarding and broader organization lifecycle |
| Assignment topology | Stable assignment/leg/task IDs and explicit prerequisites | Additional transport patterns and authorized dependency semantics |
| Artifacts and commitment | Stable artifact IDs, revisions, released snapshots, exact customer responses | More document types/templates and external signing |
| Evidence | Authorized attachment reference, actor/source/mode, business time and record time | Carrier/port/customs event adapters |
| Agent execution | Scoped session/job, input basis, typed result, adoption/fencing, provider mode | Additional specialties and orchestration |
| Exceptions and change | Case/target references, approval snapshots, applied revisions, review tasks | Broader impact coverage where subsequently contracted |
| Procurement | Distinct quote/selection/confirmation, validity, evidence, optional external reference | Provider booking and confirmation adapters |
| Finance | Stable charge line, unit/currency/Decimal, basis, dispute, settlement revision | Payment/accounting integrations |
| Closure | Domain predicates, decision audit, operational/financial distinction | Additional business policies without rewriting historical conclusions |
| Environment/data origin | Feature flags, isolated fixture metadata, labeled synthetic/manual/live sources | Staged real-user rollout and integration verification |

Specify command/response and state semantics only where needed by current implementation. Do not build empty adapters for hypothetical providers or permanently multiply tables without a current data need.

### 11.2 R1 remainder ledger

Create DEMO-1-r1-remainder.md with one row for every AC-01–AC-26 and every paused item P-01–P-13.

Required columns:

- original requirement and authoritative source;
- DEMO-1 coverage boundary;
- actual evidence/result and candidate SHA;
- uncovered subcase or business breadth;
- remaining implementation versus remaining verification;
- proposed next milestone;
- dependency/access requirement;
- responsible owner;
- re-entry condition and later acceptance criterion.

“Not in DEMO-1,” “not yet tested,” and “already implemented” are separate facts. Do not mark an original AC fully passed solely because its selected demo subcase passed.

### 11.3 Original acceptance traceability

This table fixes scope mapping, not execution results. Actual outcomes must be supplied in the remainder ledger.

| Original ID | DEMO-1 treatment | D1 mapping / later accounting |
| --- | --- | --- |
| AC-01 Legacy regression | Keep current gates and affected legacy walkthrough coverage | D1-16; track any unexecuted legacy surface |
| AC-02 Entry separation | Required for prepared authenticated customer/manager identities | D1-01/02/14; public onboarding remains separate |
| AC-03 Claim concurrency | Retain/extend single-owner proof | D1-02 |
| AC-04 Context safety | Required across demonstrated assignments/sessions | D1-04/11/14 |
| AC-05 Shared views | Required for the live quotation and edited artifacts | D1-04 |
| AC-06 Manual completeness | Required across the full selected lifecycle | D1-05 |
| AC-07 Agent quality | AG-02 demonstrated; existing AG-01 protected | D1-03; remaining adopted two-Agent breadth tracked |
| AC-08 Invalid output | Rejection/flagging without confirmed mutation required | D1-03/12 |
| AC-09 Proposal vs action | Required throughout | D1-03/06/07/10 |
| AC-10 Authorization | Required for every introduced endpoint/action and relevant old surface | D1-11 |
| AC-11 Version conflict | Required for edit, adoption and customer response | D1-04/07/12 |
| AC-12 Change propagation | Quantity scenario demonstrated; preserve adopted mapping/tests | D1-09; do not claim general impact graph |
| AC-13 Dependencies | Existing fixed prerequisites, cycle checks, and demonstrated hard gates | D1-06/13; general DAG remains outside scope |
| AC-14 Procurement truth | Evidence/validity/capacity rules and existing reservation conflict semantics preserved | D1-06/09; additional supplier breadth tracked |
| AC-15 Restart recovery | Required for the selected durable job/action path | D1-12; advanced operations deferred |
| AC-16 Manual takeover | Required end-to-end, including late output | D1-12 |
| AC-17 Attachments | Existing supported extraction and safe manual fallback | D1-03/05/11; expanded OCR deferred |
| AC-18 Prompt injection | Retain/add scoped adversarial attachment test | D1-11/12 |
| AC-19 Finance | Required for selected charge/dispute/settlement flow | D1-10/13; further currencies/integrations excluded |
| AC-20 Handover/closure | Actual closure conditions and concurrent blocking check required | D1-13, including DR-0013 10/20 |
| AC-21 UI resilience | Required on all demonstrated pages and transitions | D1-14; broader device matrix deferred |
| AC-22 Feature flag | Preserve off behavior and authorize all enabled capabilities | D1-11/16 |
| AC-23 Customer confirmation | Required released exact-version acceptance | D1-07 |
| AC-24 Audit | Required for consequential actions and retained history | D1-04/07/09/10/12/13 |
| AC-25 Upgrade | Required for every schema change and supported upgrade path | D1-16 |
| AC-26 Route/data claims | Synthetic/manual/live distinctions required | D1-03/08/10/15/17 |

### 11.4 Later milestone boundaries

The next contract shall start from the accepted DEMO-1 commit, migrations, service contracts, and evidence—not from the old v0.5.1 tag or a rewritten demonstration.

| Future work order | Purpose | Entry material |
| --- | --- | --- |
| R1 completion/readiness contract | Close the remaining adopted R1 business, two-Agent, verification, and intended real-user readiness gaps | Accepted DEMO-1 evidence plus AC remainder ledger |
| Optional external-integration contract(s) | Add named live booking/signature/payment/data integrations with actual access and bounded claims | Stable evidence/domain interfaces and provider-specific requirements |
| Optional capability-expansion contract | Additional Agent specialties, broader multimodal patterns or impact graph | Explicit new scope and validation criteria |

Do not automatically enlarge R1 to require every optional integration or all five Agents. The adopted R1 plan remains the source for its original obligations.

DEMO-1 acceptance does not authorize any future work order, public rollout, or real business commitment. Future contracts must state their own entry conditions, required data/provider access, acceptance tests, rollout/rollback, and retained limitations.

## 12. Required implementation handoff

The development team shall deliver these repository-native materials with the implementation. This document defines them; it does not assert that they already exist.

| Deliverable | Suggested location | Minimum content |
| --- | --- | --- |
| Milestone contract | docs/entrust/milestones/DEMO-1-contract-v1.0.md | Adopted version and source references |
| Execution plan | docs/entrust/milestones/DEMO-1-plan.md | Five packages, work items, dependencies, estimates, current exits |
| API/data delta | docs/entrust/milestones/DEMO-1-interface-delta.md | Added/changed commands, states, fields, migrations and permissions |
| Runbook | docs/entrust/milestones/DEMO-1-runbook.md | Build/start, account setup, model modes, fixture/reset, recovery |
| Walkthrough | docs/entrust/milestones/DEMO-1-walkthrough.md | Main script, roles, expected results, truthful fallback |
| Acceptance report | docs/entrust/milestones/DEMO-1-acceptance.md | D1 result/evidence per row, exact candidate, CI, limitations and final HO verdict |
| R1 remainder ledger | docs/entrust/milestones/DEMO-1-r1-remainder.md | All original ACs and all paused items with re-entry conditions |
| Evidence index | docs/entrust/milestones/DEMO-1-evidence-index.md | Links to logs/screenshots/video/test output and environment metadata |

Equivalent existing documents may be updated and linked instead of duplicated, provided every required field remains easy to locate.

Evidence must not contain passwords, access tokens, model keys, or unrelated customer data. Large recordings may be stored outside Git with stable authorized references. Public sharing is not authorized by this contract.

## 13. Completion statement and acceptance record

Before asking HO to accept DEMO-1, the developer shall provide a concrete runnable candidate and completed evidence, not a promise to finish after approval.

The submission shall state:

1. Candidate commit and environment.
2. BP-01–BP-05 completion and D1-01–D1-18 results.
3. Actual live-model and offline/manual evidence.
4. Closed blockers and remaining non-blocking issues.
5. Exact paused scope and proposed next work order.
6. Which master ACs have sufficient evidence and which remain partially covered/unverified.

Use the following record after execution:

| Acceptance field | Value to record at review |
| --- | --- |
| Candidate commit / environment | Supplied with candidate |
| Demonstration date / witness | Supplied at witnessed run |
| Mandatory D1 results | Evidence-backed results, no assumed PASS |
| Live AI / manual fallback | Separate observed results |
| Remaining limitations | Linked issue/remainder entries |
| HO verdict | Accepted / not accepted, with date and basis |
| R1 status | Independently assessed; not inferred from DEMO-1 |
| Public-release status | Independently authorized; not inferred from DEMO-1 |

No dates or acceptance results are prefilled as if execution had occurred.

## 14. Source references

These links pin the planning basis. Later implementation shall record its own candidate commit and changed evidence.

- [Verified repository baseline](https://github.com/mo21cn/mo21cn-pinglu-didi/tree/e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f).
- [Repository implementation rules](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f/AGENTS.md).
- [Master PRD v1.0](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f/docs/entrust/Entrusted_Shipping_Agent_IM_PRD_v1.0.md).
- [Delivery plan v1.7](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f/docs/entrust/MVP开发与交付计划_船好多委托发货AgentIM_v1.7.md).
- [A2 slice plan and S3 closure boundary](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f/docs/entrust/04-A2期切片计划.md).
- [Decision-record directory](https://github.com/mo21cn/mo21cn-pinglu-didi/tree/e9a4a9d7ee86b950d60b137fd7583cecc4d28b0f/docs/entrust/decisions).

### Revision history

| Version | Date | Change |
| --- | --- | --- |
| 1.0 | 2026-09-15 | Establish DEMO-1 endpoint, five business packages, non-negotiable rules, explicit pauses, acceptance matrix, and later milestone interfaces |

