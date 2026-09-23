# DEMO-1 to Live UI Release — Work Order and Milestone Addendum

| Control | Value |
|---|---|
| Working parties | Codex: implementation and evidence; HO + assistant: scope review; Human Operator (HO): merge/release authorization and acceptance |
| Prepared | 2026-09-21 |
| Status | Recommended execution order for HO adoption; no implementation, merge, deployment, or acceptance is asserted by this document |
| Purpose | Move from an assisted demonstration to an independently usable, deployed release for a defined initial user group |
| Reviewed repository | mo21cn/mo21cn-pinglu-didi |
| Reviewed develop | a1791ea3ce6ac97176b3c2d0fea0c839b5c6abcb |
| Reviewed PR | #188, head 2e25c408388b4b8338f6773e1b24c4002dc6f79f; open, mergeable clean, six checks successful at inspection |
| Suggested repository location | docs/entrust/milestones/DEMO-1-live-ui-work-order.md |

## 1. Endpoint and scope

The endpoint is a limited-scope live release: admitted real customers and authorized staff can complete the entrusted-shipping workflow through the product UI against a deployed, persistent backend. They do not depend on a developer workstation, developer-supplied business IDs, direct HTTP commands, console scripts, or database edits during normal operations.

The initial audience is a defined set of admitted users and organizations. Broad public self-registration, automated provider discovery, production-scale HA, and external payment/signature integrations remain outside this work order. This is an explicit release boundary, not a claim that the entire original R1/master contract is complete.

Two checkpoints apply:

1. **UI-ready candidate:** the accepted workflow is usable end to end through visible product controls, with no developer business writes. This is a development checkpoint, not the final endpoint.
2. **Live release accepted:** the same candidate is deployed to the approved target, the intended client distribution channel is available to the intended users, real identities work, and HO witnesses the final workflow. A developer-tools preview, LAN-only session, or pending platform publication is not a substitute.

If the target includes unrestricted public registration or integrated funds movement, that is a larger release scope. Do not silently claim those capabilities from this work order.

## 2. Verified baseline and corrections

The supplied interface inventory is useful but is not proof of independent UI completion:

- Its headline reports ten of thirteen steps as UI-accessible. Several steps remain partially assisted; this ratio is not a completion percentage.
- It lists five action gaps while some headings still say four. The delivery package additionally identifies charge quantity as an API-assisted field. The reviewed finance submit handler sends amount and basis but no quantity or unit.
- Existing task schemas support fixed prerequisites and required evidence. Adding their controls does not require a new dependency engine.
- O-1b currently excludes source-verification and takeover controls. These were accepted demonstration boundaries; retaining them prevents independent operation of the affected paths.
- PR #188 adds observation anchors. It does not implement the missing business actions.
- The reviewed miniapp request resolver defaults physical devices to a development-machine LAN address and allows a local-storage override. The reviewed Compose file explicitly describes local development and sets APP_ENV=development. These are not evidence of a deployable release configuration.
- Settlement receipt evidence currently has only labeled_sample mode. Such records cannot be relabeled as real financial evidence when admitting real business data.

Code inspection was performed for planning. No device run, deployment, or pytest execution is claimed by this document.

## 3. Recommended decisions

| ID | Decision | Boundary |
|---|---|---|
| D-01 | Implement P-UI1 now, including task prerequisites, required evidence, and Agent-job amount. Include omitted charge quantity/unit in the same capability backlog. | Reuse existing fields and commands; preserve unknown values and monetary precision. |
| D-02 | Amend O-1b for this release: provide source verification to authorized staff and task takeover to authorized operators. | This is not unrestricted customer self-service. Preserve scoped server authorization, audit records, and existing business checks. |
| D-03 | Replace current “no verification/takeover anchor anywhere” assertions with role-specific positive and negative assertions. | Preserve old logs at their original commit; do not rewrite historical evidence. Current executable tests must follow the adopted current contract. |
| D-04 | Recommend merging #188 under the existing HO merge procedure after confirming the reviewed head and required checks. | Its documentation is an interim baseline. Its statements that O-1b is permanently unchangeable do not govern the new milestone. No merge is executed by this document. |
| D-05 | Put function completion before anchor cleanup and cosmetic work. Combine necessary UI changes before final integrated device evidence. | Run targeted checks per change; avoid a full device main-chain run for every template-only edit. |
| D-06 | Keep the native file picker and camera. Verify actual file selection/upload manually on a supported physical device. | OS automation remains deferred. A tool limitation does not exempt the product feature from human validation. Built-in samples remain auxiliary. |
| D-07 | Produce the operating walkthrough alongside implementation, not as a separate approval-dependent project. | “What to do when blocked” must refer to a working UI action or a named authorized operator, not developer API intervention. |
| D-08 | Reopen the minimum real-user admission and deployment work paused by DEMO-1. | Keep broad onboarding automation, HA, and third-party integrations deferred; do not defer basic identity, authorization, persistence, recovery, or client access. |
| D-09 | Separate demonstration signature/payment evidence from actual manually recorded offline evidence. | No claim of provider-verified e-signature or bank settlement. Sample evidence must not satisfy completion gates on real-business records. |

These decisions form one scope update. Routine implementation choices inside the adopted scope do not require additional HO product decisions. Existing merge and release authorization policies remain in force.

## 4. Ordered work packages

### WP-0 — Establish one candidate and expose external dependencies

**Owner:** Codex; HO supplies only organization-controlled resources and release decisions.

1. Refresh the baseline after integration; distinguish current candidate, earlier evidence commits, and historical statements.
2. Correct the inventory to include six known assisted actions. Check reachable entry, permission, valid input, submission, result, reload, and failure recovery; anchors alone are insufficient.
3. At the start of the work, identify the actual deployment target, release channel, domain/TLS setup, AppID and platform access, real-login credentials, admitted organization/user setup, database/attachment storage, and model configuration. List missing external resources once with owner and affected exit.
4. Inspect the existing release/authentication/migration paths and reuse them. Report missing implementation, already-working capability, and unverified capability separately.
5. Give effort ranges by WP, separating active engineering effort, device execution, HO resource provisioning, and external publication waiting. Do not invent a calendar promise from PR/test counts.

**Exit:** one executable backlog and a named release target. External waiting does not stop independent capability work.

### WP-1 — Complete the missing UI fields

**Priority:** first implementation package.

- **Task prerequisite:** select from readable tasks on the same assignment using human-readable labels; do not require users to type internal IDs. Preserve server enforcement against invalid/cross-assignment relationships and cycles where prohibited by the existing contract. Support correction through the existing prerequisite command where applicable.
- **Required evidence:** choose existing evidence categories, display what each task requires, and show the path to attach missing evidence. Do not create a new workflow or evidence taxonomy.
- **Agent-job amount:** add an explicitly named business amount input at the relevant operation. Do not expose a generic JSON editor or present it as model-call cost. Align currency/precision and absent-versus-zero semantics with the applicable quote contract; do not invent an amount.
- **Charge quantity and unit:** provide the existing quantity/unit fields where the business action needs them. Do not copy the assignment quantity into every charge or silently recalculate agreed amounts after a quantity change. Show the recorded basis and preserve version semantics.

**Exit:** steps 4, 6, and the affected financial action can be performed without an API helper. Demonstrate input, server persistence, and reload, not just control visibility.

### WP-2 — Complete authorized source verification and takeover

**Priority:** immediately after, or independently alongside, WP-1 where changes do not conflict.

**Source verification:**

- List pending source claims for the exact artifact revision and expose the available source material to the authorized reviewer.
- Record the object, method, supporting reference/observation, and outcome using existing domain semantics. Operator identity and recording time are server-authored.
- Failure or incomplete verification continues to block release. A new revision must not inherit an unrelated verification as blanket approval.
- Do not use a universal “mark all verified” bypass. Customer visibility remains limited to the adopted customer projection.

**Task takeover:**

- Provide an authorized takeover action showing the task and the consequences; record a reason and explicit confirmation, using the existing takeover command and audit path.
- Preserve execution-generation fencing: an earlier Agent execution must not overwrite the new human-controlled state.
- Verify unauthorized denial at the server even if a request is forged outside the UI.

Use existing server capability projections where available. If a projection is missing, add the smallest scoped capability response; do not infer permission from an organization-wide role alone. Reuse current pages unless their navigation or access boundaries require a separate view.

**Exit:** an authorized user can resolve a real blocked-release condition and take over a real task through the UI; unauthorized users cannot. Old zero-anchor tests are replaced by meaningful permission and behavior tests.

### WP-3 — Make daily use independent of developers

1. Verify login, role entry, organization/assignment selection, customer access, logout/re-entry, and navigation from the normal home entry. Do not navigate directly to hidden routes to prove user reachability.
2. Support the admitted real users and organizations without demo identity impersonation. If necessary, implement a minimal restricted admission/authorization screen using existing models; no generic administration framework is required. One-time deployment bootstrap is distinct from normal business operation.
3. Verify native file selection and upload on a supported physical device, including cancel, failure/retry, extraction status, and manual transcription fallback. Do not store a user's actual credentials in evidence.
4. Preserve a complete manual business path when the model is continuously unavailable. No mock successful proposals, hidden API writes, or temporary model restoration may supply its missing business facts.
5. Repair blocking usability issues: unavailable primary actions, unreadable text, unclear permissions, silent submission failures, lost inputs, retry duplication, and missing recovery navigation. Add needed test anchors as part of these changes.
6. Record the UI walkthrough with exact roles, start conditions, expected business facts, and visible recovery actions. Visual redesign and exhaustive styling inventories are deferred.

**Exit:** a user can operate from the entry screen, recover from supported failures, and continue the business lifecycle without developer intervention.

### WP-4 — Deliver the minimum live operating environment

Start deployment preparation during WP-0; do not leave external dependencies until the final day.

**Configuration and availability**

- Provide explicit development/demo/release configurations. Release clients use the approved HTTPS service endpoint, not the LAN fallback or an arbitrary developer storage override.
- Run the backend independently of the development workstation/IDE, with the selected production database and persistent attachment storage. Configure secrets outside the repository and remove development defaults from the release profile.
- Real authentication and scoped authorization must work for distinct admitted accounts. Demo identity shortcuts, reset scripts, and fixture bootstrapping must not be exposed as production operations.
- Check required client/platform configuration against the actual publishing target and current platform requirements during implementation. Submit/publish through the established authorized process. If external review is pending, report release blocked rather than relabeling a preview as released.
- Verify restart persistence, a usable backup/restore path for database and attachments, required migrations, health/log diagnostics, and a documented rollback or safe roll-forward for the actual change. Reuse existing mechanisms; no operations platform build is required.

**Real-business evidence boundary**

- Audit signature and receipt handling before allowing real assignments to reach completion. Current labeled_sample records remain demonstration records.
- Implement a small, explicit manual-offline-evidence mode if real operations use offline signing or payment. Bind evidence to the exact relevant contract/settlement version and authorized operator; include source material, business occurrence date, amount/currency where relevant, and review result.
- Do not migrate old sample records into actual evidence. Do not treat manually uploaded evidence as independently verified electronic signature or bank confirmation.
- Real-data completion checks must reject sample evidence. Keep demo workflows in a separately identified environment or equivalent enforceable data boundary.
- Payment-provider integration and electronic-signature-provider integration remain deferred. Real financial use is blocked until the manual evidence path and its checks exist; a label change alone is insufficient.

**Exit:** the release environment and real-record semantics are usable for the stated scope. Missing external access and unsupported live operations remain explicit blockers, not accepted demo limitations.

### WP-5 — Freeze, witness, and release

1. Run affected tests during implementation and existing required CI on the final candidate. Do not relax domain rules to reproduce former green counts.
2. Freeze the integrated candidate and configuration. Run one complete UI main chain on one new assignment with distinguishable authorized roles, including the changed fields, verification, takeover, commercial/financial outcomes, and pure-read reload.
3. Run the manual alternative on a separate new assignment with the model persistently unavailable. Reuse unchanged valid evidence where its scope truly applies; obtain fresh evidence for new UI recovery steps.
4. Perform targeted negative checks for permissions, unmet prerequisites, unverified publication, stale versions, takeover fencing, and incomplete closure. The release must demonstrate refusal as well as successful completion.
5. Perform the physical-device file action and deployed-environment persistence/recovery checks. Human evidence is acceptable for native dialogs; lack of OS automation does not block an otherwise observed successful manual action.
6. HO follows the walkthrough from the normal entry on the deployed client. The developer may observe and explain; if a developer must perform an out-of-band business write, that path has not passed.
7. After necessary fixes, rerun the affected checks. Repeat the entire chain only when the changes materially affect cross-step behavior. Record exact code/configuration provenance and the remaining evidence applicability.

**Exit:** all mandatory checks below pass and HO records the final verdict. Merge, deployment, and acceptance remain separate events.

## 5. Release acceptance matrix

| ID | Required outcome | Evidence |
|---|---|---|
| L-01 | Actual deployed client and backend reachable by intended users without developer LAN/IDE dependence | Target/client version, configuration fingerprint, normal-entry observation |
| L-02 | Real identity, explicit organizational authorization, and customer/staff isolation | Distinct-account positive/negative cases; no demo impersonation |
| L-03 | One assignment completes the required UI workflow without hidden business writes | UI action record plus authoritative ID/version correlation |
| L-04 | Six identified assisted actions are resolved, including authorized verification/takeover and charge quantity | UI submission, server response, reload, permission evidence |
| L-05 | A real selected file is uploaded and usable; cancellation and supported failures recover | Supported physical-device observation; sample path separately labeled |
| L-06 | Model-unavailable manual path reaches the business endpoint | Failure mode recorded throughout; same assignment retained |
| L-07 | Real-record commercial/financial gates cannot be satisfied by sample evidence | Manual-offline mode tests and sample-rejection checks |
| L-08 | Data/attachments survive restart, recovery is demonstrated, release configuration is isolated | Restart/readback and bounded restore/rollback evidence |
| L-09 | Final candidate meets existing mandatory CI and new scoped behavioral checks | Candidate SHA, check results, explicit unexecuted items |
| L-10 | HO independently operates the deployed supported scope and receives a usable runbook | HO witness record and final verdict; publication state stated explicitly |

Required unexecuted items remain open. Executed failures remain failures with a cause; they do not become NOT_RUN because they are inconvenient. An automation limitation may coexist with separately recorded human PASS for the same product outcome.

## 6. Execution discipline and deferred work

**Sequence:** WP-0 → WP-1/WP-2 → WP-3 → WP-5, with WP-4 preparation starting at WP-0 and its implementation completed before final witnessing. Use small coherent PRs; do not create an HO checkpoint for every field, label, selector, or test correction.

Do not schedule anchor-count optimization, a new test framework, OS automation, repeated exhaustive device runs, broad styling, additional autonomous Agents, universal dependency graphs, or third-party integrations ahead of a missing mandatory business action.

Maintain one current execution section and one current acceptance view in the existing milestone documents. Archive superseded plans as history. Keep historical evidence immutable, but update active tests and current user instructions when the adopted behavior changes.

Progress reports must state: completed user-visible exit; remaining required exit; failed or unexecuted evidence; next concrete implementation action; external dependency and owner, if any. PR counts, test totals, and selector totals are supporting engineering data, not progress percentages.

## 7. Handoff and sources

Update existing documents instead of creating another parallel documentation suite: interface gaps, next-stage plan, API delta, runbook, walkthrough, acceptance, evidence index, and R1 remainder ledger. This work order is the scope addendum; it does not retrospectively certify DEMO-1 or erase original requirements.

Reviewed sources at the PR #188 head:

- [PR #188](https://github.com/mo21cn/mo21cn-pinglu-didi/pull/188)
- [Interface gaps](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/docs/entrust/milestones/DEMO-1-interface-gaps.md)
- [Next-stage plan](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/docs/entrust/milestones/DEMO-1-next-stage-plan.md)
- [Milestone contract, including pauses and change control](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/docs/entrust/milestones/DEMO-1-contract-v1.0.md)
- [Delivery package](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/docs/entrust/milestones/DEMO-1-delivery-package.md)
- [Client endpoint resolver](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/miniapp/utils/request.js)
- [Local-development Compose](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/deploy/docker-compose.yml)
- [Finance UI submission](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/miniapp/pages/entrust/finance/finance.js)
- [Settlement evidence semantics](https://github.com/mo21cn/mo21cn-pinglu-didi/blob/2e25c408388b4b8338f6773e1b24c4002dc6f79f/backend/app/modules/entrust/settlement.py)

---

# 仓库内执行记录（现行执行段）

> 本节是 WP 的**唯一现行执行段**（work order §6 要求：仓库里只保留一份当前执行视图）。
> 上方原文来自 HO，⛔ 不改写；本节记录**执行事实**，每完成一个 WP 就地更新。
> 起始：2026-09-21

## E-0 · WP-0 执行记录（2026-09-21）

### E-0.1 基线刷新（三类值分开写，⛔ 不混用）

| 类别 | 值 |
| --- | --- |
| **当前候选** | `2e25c40`（PR **#188** head：界面缺口清单 ＋ 24 处锚点；**未合并**） |
| **上一个已合并** | `a1791ea`（`develop`；含 #186 squash `5979218`、#187 squash `a1791ea`） |
| **历史证据提交** | `26f8a299`（#185 取证轮）／`0baad07`（#186 段A/段B 无模型通路）／`a1791ea`（#187 交付包＋日期夹具） |
| **历史陈述（已被本工作单取代）** | `DEMO-1-interface-gaps.md` 里"核验/接管 ⛔ 不可补（O-1b 永久排除）"的表述 ⇒ 由 **D-02** 取代为「**授权角色可用**」；旧日志按 D-03 保留在原提交，⛔ 不改写 |

### E-0.2 辅助动作订正：**5 → 6 项**（工作单 §2 指出）

| # | 动作 | 界面位置 | 后端契约 | 现状 |
| --- | --- | --- | --- | --- |
| 1 | 任务**前置关系** | `detail.wxml` 记录任务表单 | `TaskCreate.precondition_task_id` ✔ 已存在 | ❌ 缺入参 |
| 2 | 任务**必需证据** | 同上 | `TaskCreate.required_evidence` ✔ 已存在 | ❌ 缺入参 |
| 3 | **Agent 作业金额** | `session.wxml` 发起作业 | 作业入参 | ❌ 缺入参 |
| 4 | **来源核验登记** | 无入口 | `POST /entrust/artifacts/{id}/source-checks` ✔ 已存在 | ⛔ O-1b → **D-02 改为授权可用** |
| 5 | **任务接管** | 无入口 | 既有接管命令 ✔ 已存在 | ⛔ O-1b → **D-02 改为授权可用** |
| 6 | **费用 `quantity` / `unit`** | `finance.wxml` 费用表单 | `ChargeCreateIn.quantity`／`unit` ✔ 已存在（`schemas.py:1999-2000`） | ❌ 缺入参（工作单 §2 补入） |

⭐ **六项全部是"复用既有契约"** ⇒ 无新增依赖引擎、无新增证据分类（与 D-01 的边界一致）。

### E-0.3 部署目标与**外部资源**（需 HO 提供 —— 一次性列清，含 owner 与受影响的 exit）

| 资源 | 仓库现状（已核实） | Owner | 缺了会卡住 |
| --- | --- | --- | --- |
| 部署目标（主机/环境） | 未定 | **HO** | L-01 |
| 发布渠道（小程序 AppID／平台发布） | `miniapp/` 有代码，**无发布配置** | **HO** | L-01／L-10 |
| **域名 ＋ TLS（HTTPS 合法域名）** | ❌ 缺：`utils/request.js` 只有 `127.0.0.1` 与**硬编码 LAN IP** `192.168.0.178` | **HO**（域名/证书/备案） | L-01 |
| 真实登录凭据（准入账号） | 未提供 | **HO** | L-02 |
| 准入组织／用户清单 | 未提供 | **HO** | L-02 |
| 生产数据库 | ❌ 缺：`deploy/docker-compose.yml` 是**本地开发**编排（`APP_ENV: development`、root 明文） | **HO** | L-08 |
| 附件持久存储 | ❌ 缺：同一 compose **无附件卷** | **HO** | L-08 |
| 模型配置（真 Key ＋ 端点） | 本机 `backend/.env.local` 有 Key；**release 需独立配置** | HO 提供资源，Codex 落地 | L-03／L-06 |
| 支付／电子签 provider | **明确推迟**（D-09／WP-4） | — | 不阻塞（走人工离线证据） |

### E-0.4 发布／认证／迁移路径（三档分开报，⛔ 不合并成一句"已具备"）

| 档 | 内容 |
| --- | --- |
| ✅ **已可用（本机实测过）** | 迁移：`backend/migrate.py` ＋ `migrations/`（CI 在 SQLite 与 MySQL 8.0 两套上跑）；认证：角色进入链路（首页 `enterRole`）；权限：`authz.py` 的权限码与组织分域；静态契约：8 个 `verify_*.js` |
| ❌ **缺失（要新建）** | dev／demo／**release 三档配置**；HTTPS 端点配置（现只有回环＋LAN）；后端脱离 IDE 的独立运行编排；备份／恢复脚本；**最小准入屏**（WP-3）；**人工离线证据模式**（WP-4/D-09） |
| ⚠️ **未验证（不许当已具备）** | 重启后的数据持久化（本机从未验证）；附件存储持久化；真实账号的分域授权；微信平台侧的发布配置核对 |

### E-0.5 工作量范围（**范围，不是日历承诺** —— ⛔ 不用 PR 数/测试数推日期）

| WP | 主动工程 | 设备执行 | HO 资源 | 外部等待 |
| --- | --- | --- | --- | --- |
| WP-1 字段 | 小（4 项均为复用既有契约） | 0.5 轮 | 无 | 无 |
| WP-2 核验＋接管 | 中（含断言按 D-03 改造） | 1 轮 | 无 | 无 |
| WP-3 独立使用 | 中（含最小准入屏 ＋ 物理设备验证） | 1–2 轮 | 准入账号 | 无 |
| WP-4 live 环境 | 中（三档配置 ＋ 证据边界） | 1 轮 | 域名/证书/主机/DB/存储/AppID | **平台审核（不可控）** |
| WP-5 冻结见证 | 小 | 2 轮（主链 ＋ 人工替代） | **HO 见证** | 无 |

### E-0.6 执行顺序（照工作单 §6）

```
WP-0（本段）→ WP-1 ∥ WP-2 → WP-3 → WP-5
WP-4 的准备从 WP-0 起并行，实现须在最终见证前完成
```

* ⛔ **不做**（工作单 §6）：锚点数量优化、新测试框架、OS 自动化、反复全量设备跑、大范围样式、更多自主 Agent、通用依赖图、第三方集成 —— 都排在"缺失的必需业务动作"之后。
* ⛔ **不建 HO 检查点**：字段／文案／选择器／局部测试修复（D-05）。
* ⛔ **仍要 HO 拍板**：PR 合并、外部资源、正式发布。

### E-0.7 本段结论

* **可执行 backlog** ＝ E-0.2 的 6 项 ＋ WP-3 的 5 项 ＋ WP-4 的配置与证据边界。
* **命名发布目标** ＝ 待 HO 在 E-0.3 里指定（部署目标＋渠道）。⛔ 在指定前，WP-4 只能做"与目标无关"的部分（三档配置骨架、脱离 IDE 的运行方式），⛔ 不得声称"可发布"。

## E-0.8 外部资源核验（HO 2026-09-21 提案）

> HO 提议：**微信云开发（CloudBase）** —— 云数据库 ＋ 云存储，`wx.cloud.init` 连接，
> "无需自行搭建服务器"。下面逐项核验（结论按**本项目实际形态**给，⛔ 不按通用小程序建议给）。

### E-0.8.1 逐项结论

| # | HO 提案 | 结论 | 依据 |
| --- | --- | --- | --- |
| 1 | **云数据库**替代后端库 | ⛔ **不可行** | 云数据库是**文档型 NoSQL**（类 MongoDB）：**不支持多表关联／子查询**，复杂事务只能靠云函数模拟。本项目的 `entrust` 模块建立在**关系型语义**上：SQL 迁移（CI 在 SQLite 与 **MySQL 8.0** 上真跑）／**乐观锁** `revision_no` 的并发用例（`test_complete_race_exactly_one_winner`）／**幂等键** ＋ 唯一约束／组织分域与客户白名单投影。换库 ＝ 重写数据层 ＋ 业务规则失效 ＋ CI 与 1015 项单测全废 ⇒ 直接违反 D-01「复用既有契约」与 WP-4「复用既有机制」 |
| 2 | **不需要自有服务器** | ✅ **可行，但要用对形态：云托管（CloudBase Run），不是云函数** | 云托管跑**容器**且原生支持 FastAPI（`uvicorn main:app --host 0.0.0.0 --port $PORT`）；本仓 `backend/Dockerfile`（`python:3.12-slim`、`APP_ENV=production`）**可直接部署**。云托管可经**私有网络**访问 VPC 内的 **MySQL／Redis** |
| 3 | ⭐ **免域名／免备案** | ✅ **成立**（微信官方文档明示） | 「如使用**微信云托管**作为后端服务，则可**无需配置通讯域名**」（小程序侧走 **`callContainer`**／`connectContainer` 的微信私有协议）⇒ 免 HTTPS 域名、免服务器域名白名单、免证书。**这正是"开发测试版本、暂不需要自有服务器"的契合点** |
| 4 | **附件用云存储**（`wx.cloud.uploadFile` 直传） | ⚠️ **存储可行，直传不可行** | 附件在本项目要**经服务端**：魔数嗅探（`extraction.sniff_media`，⛔ 不听信客户端声明的 MIME）／提取状态 `extract_status`（`done`／`needs_transcription`）／人工转录落库／与委托·成果绑定／客户白名单投影。直传会让后端**拿不到字节** ⇒ 嗅探·提取·转录链断掉（等于推翻 D1-05 的附件分支）。✅ 正确形态：小程序 →（`callContainer`）后端 → 后端写对象存储 → 回传附件 id |
| 5 | **备案号** 桂ICP备2026021348号-1X | ⚠️ **本方案下不是必需项**；且**无法核验真伪** | ① 备案是**域名级**的：白名单要填**已备案的具体域名**，备案例号本身填不进去；② 走云托管 ＋ `callContainer` ⇒ **不需要域名**，所以备案例号在本轮**不必需**（将来接自有域名/非微信客户端时才需要）；③ ⛔ 我只能查**域名**的备案状态，**无法凭备案号核验真伪**（须在工信部备案系统按域名查）；④ 格式提请核对：`-1X` 后缀不常见（通常 `-1`／`-2`），建议照备案系统原样抄 |
| 6 | **AppID** `wx4a57f29bc38ca11d` | ✅ **已核验一致** | `miniapp/project.config.json:22` 就是它。⚠️ 需确认**同主体**：云开发环境必须与该 AppID **同一主体**；`callContainer` 也要求同主体 |

### E-0.8.2 由此确定的路径（WP-4 的形态变了，但**不减少**要求）

```
小程序 ──(wx.cloud.callContainer，微信私有协议，免域名)──▶ 云托管（现有 FastAPI 容器）
                                                              │
                                                   私有网络 ──┴──▶ 关系型 MySQL（保留 SQL/事务/乐观锁/幂等）
                                                              └──▶ 对象存储（附件，**经后端**写入）
```

* ✅ **保留**：现有 `backend/Dockerfile`、全部 SQL 迁移与并发语义、CI 的两套 DB 验证。
* ✅ **免去**：自有服务器、HTTPS 域名、证书、域名白名单（⇒ 也免去"域名备案"这个等待项）。
* ⛔ **必须新增**（WP-4 的工作量）：
  1. 小程序接入层：`wx.cloud.init({env})` ＋ 把 `wx.request` 换成 **`wx.cloud.callContainer`**
     （当前仓库 **0 处**云开发痕迹，已核实）—— ⚠️ **并须实测走查工具（模拟器）能否点通 `callContainer`**，
     否则会重演"关键路径不可验证"的老问题；
  2. dev／demo／**release 三档配置**（compose 仍是 `development`，须补 release 档）；
  3. 关系型 MySQL 实例 ＋ 附件对象存储的配置与备份/恢复；
  4. **费用**：云托管按量计费（可缩容到 0）＋ MySQL 实例另计费 ⇒ ⛔ **不是"免费额度"覆盖**，
     需 HO 确认预算；我**不能**替你承诺价格（以腾讯云当期价目为准）。

### E-0.8.3 仍然缺的（E-0.3 里的项，⛔ 一项都没被这份提案消掉）

| 项 | 状态 |
| --- | --- |
| 部署目标（云开发环境 ID） | ✅ **已开通**（见 E-0.9） |
| 真实登录凭据（准入账号） | ❌ 待 HO |
| 准入组织／用户清单 | ❌ 待 HO |
| 模型配置（真 Key） | ⚠️ 本机 `.env.local` 有；**release 要独立配置**（仓库外） |
| 发布渠道／类目资质 | ⚠️ 体验版不需要提审；**正式版**需要（属"正式发布决策"） |
| 附件存储形态 | ⚠️ 待定：对象存储（推荐）／云托管持久卷 |

## E-0.9 云开发环境已开通（HO 2026-09-23）＋ 接入层落地

### E-0.9.1 环境事实

| 项 | 值 |
| --- | --- |
| 环境 | `prod`（**上海**） |
| **envId** | `prod-d0ga9bxi6e4226222` |
| 主体 | 与 AppID `wx4a57f29bc38ca11d` 同主体（`callContainer` 的前提，✅） |
| 随环境资源 | 云托管 MySQL（模板开通时一并创建；**凭据只在云托管环境变量里配，⛔ 不入仓库**） |
| 环境内服务 | **`pinglu-backend`（尚未创建）** —— `config/env.js` 已按此名预配，创建时须同名 |

### E-0.9.2 接入层落地（本提交）

- 新增 **`miniapp/config/env.js`**：三档配置的唯一入口 —— `PROFILE`（dev/demo/release，
  当前 `dev`）、`CLOUD_ENV_ID`、`CLOUD_SERVICE`；**切档＝改一行**。⛔ 模块顶层不碰 `wx`（Node 校验环境安全）。
- `utils/request.js`：按档位分流 —— dev 走原 `wx.request` 直连（语义零改动，CI/走查基线不动）；
  **demo/release 走 `wx.cloud.callContainer`**（私有协议，免域名/免备案）。两档共用同一套
  响应/失败处理（`onResponse`/`onFail`），401 清态与错误形状不漂移；`describeError` 补
  callContainer 专属分支（env 错／服务停机／基础库过旧给"依次确认"的可执行提示）。
- ⛔ **release/demo 档禁用 Storage 覆盖**（WP-4："不用 LAN 兜底与本地存储覆盖"）——
  `dev_base_url` 只在 dev 档生效。
- `app.js`：云档 onLaunch 时 `wx.cloud.init({ env })`（`callContainer` 前置）。
- `utils/entrust.js` 上传守卫：**云档下附件上传显式拒绝**（错误信息写明 WP-4 待办），
  ⛔ 不静默把字节发到 dev 档的 LAN 地址 —— `callContainer` 不支持文件上传，合规通路
  （对象存储中转或 base64 通道，仍经服务端嗅探）待 WP-4 接通后撤守卫。
- **静态契约新增 6 条**（`verify_entrust_ui.js` §7）：三档入口存在／envId+服务名已配／
  云档禁 Storage 覆盖／按档分流／callContainer 带 env＋`X-WX-SERVICE`／app.js 云档 init ——
  全部"能失败"。

### E-0.9.3 下一步（把 release 档点亮的控制台动作，按序）

1. 云托管控制台 → 服务列表 → **创建服务 `pinglu-backend`**（空白服务），监听端口设 **8000**
   （容器内 uvicorn 就是 8000）；
2. 部署方式：上传 `backend/` 目录打包（zip，含 `Dockerfile`），或绑定 GitHub 仓库选 `backend/` 上下文；
3. 服务**环境变量**（云托管侧配置，⛔ 不进仓库）：`APP_ENV=production`、数据库连接串
   （指向环境内 MySQL 内网地址）、模型 Key、`ENTRUST_ENABLED` 等密钥类；
4. MySQL 内建库＋跑迁移（迁移 SQL 在仓，CI 已在 MySQL 8.0 验证过）；
5. 小程序侧把 `PROFILE` 切 `release` → 模拟器/真机走查 **`callContainer` 可达性**
   （本工作单点名的唯一技术风险点，实测才算数）。

> ⚠️ 诚实边界：`callContainer` 从**模拟器**点通与否**尚未实测**（服务还没建）；
> 本提交只保证"接线正确 + 契约锁死"，**"可达"要等 E-0.9.3 第 5 步的设备证据**。
