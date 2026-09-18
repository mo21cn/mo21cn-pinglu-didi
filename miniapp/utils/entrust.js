/**
 * 委托发货（经理侧）前端契约 —— 入口可见性判定 + 工作台取数与状态推导
 * （S2 前端切片 / ENT-012，对应 AC-02 入口隔离、AC-04 经理工作台、AC-21 五态）
 *
 * 为什么把逻辑放在 utils 而不是页面里
 * ----------------------------------
 * 页面的 `onLoad` 只能在微信开发者工具里跑，CI 跑不了。而"入口该不该显示"、
 * "当前该渲染哪一种状态"这两件事**恰恰是最该被守住的**：
 *   - 入口判错 → 用户点进去看 403/404，或该看到的人看不到；
 *   - 状态判错 → 空列表被显示成"加载中"、失败被显示成"没有数据"，
 *     这两种"把错误伪装成正常"的表现都不会有人报 bug。
 * 所以判定做成**不接触 wx 的纯函数**导出，由 scripts/verify_entrust_ui.js 在 Node 里
 * 用真实分支直接驱动（该脚本已挂在 CI 的前端静态契约 job 里）。
 *
 * 入口可见性由**服务端**决定，不由前端角色字段决定
 * ------------------------------------------------
 * 前端 `current_role` 存在本地 Storage 里，可被随意改写；而且"是不是经理"
 * 真正的依据是**组织成员资格 + 委托授权**，那只有服务端知道。
 * 所以这里用一个**静默探测**（`GET /assignments?view=org&size=1`）来判定：
 * 服务端放行才显示入口。这样"入口可见"与"进去能用"是同一个事实，
 * 不存在"能看到但一点就报错"的中间态。
 */

// `getToken` / `BASE_URL` 是给**上传**用的：`wx.uploadFile` 走的是另一套 API，
// 它既不自动带鉴权头、也不解析 JSON 响应 —— 这两件事必须在调用处自己做。
const { request, getToken, BASE_URL } = require('./request')

// ---------------------------------------------------------------------------
// DEMO-1 canonical 样报价单（**前端副本**）
//
// 为什么前端要带一份：会话屏的上传走 `wx.chooseMessageFile` —— 那是 **OS 级原生
// 弹层**，不在渲染树里，自动走查够不着它的选择项（技能 `miniapp-device-walkthrough`
// 的负面清单实测过）⇒ 附件入口必须另给一个"预置样本"的页内通路，否则"演示第 2 步
// 上传样报价单"永远拿不到设备证据。它同时让演示者不必先把文件塞进模拟器。
//
// ⚠️ 这份文本必须与 `backend/scripts/fixtures/DEMO1-canonical-sample-quotation.txt`
// **逐字节一致**。它由脚本从该文件生成（不是手抄），且 `verify_entrust_ui.js`
// 每次 CI 都逐格比对 —— 任何一侧改了而另一侧没跟上，静态契约先红。
// ---------------------------------------------------------------------------
const SAMPLE_QUOTE_FILENAME = "DEMO1-canonical-sample-quotation.txt"
const SAMPLE_QUOTE_TEXT = [
  "数据标签（Data label）：合成场景 / 人工录入证据",
  "DEMO-1-CANONICAL — 这不是真实客户材料，也不是真实航运报价。",
  "",
  "本文件是 **DEMO-1 canonical 夹具**（合同 §3.1；HO 0917-3 裁定一）所用的样报价单：",
  "初始货量 **800 吨**，与 `demo1_canonical.json` 里的委托输入、候选运力和变更内容配套。",
  "它与主演示脚本第 2 步、以及 CI 端到端 job 用的是**同一个文件**。",
  "",
  "⚠️ 与旧件的关系：`DEMO1-SYNTHETIC-sample-quotation.txt`（1200 吨）**保留不动**，",
  "继续作为**旧回归数据**（HO 0917-3：不要全局替换，避免既有测试基线漂移）。",
  "",
  "================================================================",
  "内河货运报价单（样件 · SYNTHETIC · CANONICAL）",
  "================================================================",
  "报价方：西江航运有限公司",
  "数据标签：合成（Synthetic）",
  "币种：CNY（人民币）",
  "",
  "一、货物",
  "货名：钢材（普通货物，非危险品）",
  "数量：800 吨",
  "包装：成捆 / 散装",
  "",
  "二、运输方案（公路 — 内河 — 公路）",
  "航线：南宁 → 贵港",
  "第一段：公路提货（厂区 → 南宁港）",
  "第二段：内河运输（南宁港 → 贵港港）",
  "第三段：公路送达（贵港港 → 卸货地）",
  "",
  "三、价格",
  "单价：45.00 元/吨",
  "计价口径：按实际计费吨结算；含装船费与卸船费",
  "预估总额：36000.00 元（按 800 吨估算，最终以实际计费吨为准）",
  "",
  "四、报价有效期",
  "有效期至：2026-12-31",
  "说明：逾期未重新确认的报价，不支撑新的确认采购。",
  "",
  "五、包含项",
  "- 船舶运输、装船、卸船；",
  "- 常规内河航段通行费用。",
  "",
  "六、不包含项",
  "- 港口建设费、滞期费（滞留费）、超期堆存费；",
  "- 保险与货损赔付；",
  "- 因委托变更（数量调整、收货港调整等）产生的重新报价。",
  "",
  "七、承运与装载口径（**变更场景的判据依赖它**）",
  "- 本报价对应的水运段按**单船承运**计，**不接受拆批**；",
  "- 因此该报价可承载的上限就是所报船舶的可用舱位，超出即需重报；",
  "- 变更后若货量超过可用舱位，原报价**不适用** —— 这不是价格问题，是装载问题。",
  "",
  "八、证据与口径声明",
  "- 本报价为**人工录入的合成数据**，没有上游业务系统对接证据；",
  "- 单价口径为「元/吨」，与数量单位（吨）一致，不存在元/柜的混用；",
  "- 承运人资质与船舶运力**均未核实** —— 本样件不宣称任何真实运力；",
  "- 本报价是**供应商侧报价**，不是对客报价；两者口径不同，不得互相替代。",
  "",
  "（样件结束）",
  "",
].join("\n")

const BASE = '/api/v1/entrust'

/**
 * 委托单状态取值域 —— 与后端 `assignments.STATUS_*` 一一对应。
 * 后端新增状态时这里必须同步，否则界面会把它显示成"未知状态"
 * （verify_entrust_ui.js 会用后端取值域做交叉断言）。
 */
const STATUS_META = {
  draft: { label: '草稿', tone: 'muted' },
  submitted: { label: '待受理', tone: 'warn' },
  claimed: { label: '已受理', tone: 'primary' },
  cancelled: { label: '已取消', tone: 'muted' }
}

/** 与后端取值域完全一致的顺序（断言用，勿随意增删） */
const STATUS_ORDER = ['draft', 'submitted', 'claimed', 'cancelled']

/** 每个状态对**经理**的下一步动作提示（未知状态不给提示，不编） */
const STATUS_HINT = {
  draft: '货主还在填写，草稿对经理不可见',
  submitted: '等待经理受理（受理是显式动作，不会自动发生）',
  claimed: '已受理，可继续安排任务与物料',
  cancelled: '已取消，不再推进'
}

/** 工作台可渲染的状态（AC-21：空 / 加载 / 错误 / 过期 / 无权限 + 正常） */
const VIEW = {
  LOADING: 'loading',
  READY: 'ready',
  EMPTY: 'empty',
  ERROR: 'error',
  EXPIRED: 'expired',
  DENIED: 'denied'
}

/**
 * 组织内角色 → 中文。键与后端 `access.ORG_ROLE_PERMISSIONS` 的键**逐字对应**。
 * 后端新增角色而这里没跟 → 界面会把"经理人"显示成默认的"成员"，这是误导
 * （用户会以为自己不能受理）。所以 verify_entrust_ui.js 拿后端源码做交叉断言。
 */
const ORG_ROLE_LABELS = {
  owner: '所有者',
  admin: '管理员',
  manager: '经理人',
  member: '成员'
}

/**
 * 权限码 → 中文。键必须**覆盖后端 `access.py` 里全部 `PERM_*` 常量**：
 * 少一个，界面就会把原始权限码（`entrust:quote:publish`）直接显示给用户。
 * 交叉断言同样在 verify_entrust_ui.js，与上面的角色表一并检查。
 *
 * 注意：这张表只用于**展示**。任何权限判定都在服务端 ——
 * 前端即使把标签写错，也不会让人多出一点权限。
 */
const ORG_PERMISSION_LABELS = {
  'entrust:view': '查看委托',
  'entrust:assignment:claim': '受理委托',
  'entrust:quote:create': '制作报价',
  'entrust:quote:publish': '发布报价',
  'entrust:task:dispatch': '派发任务',
  'entrust:settlement:create': '生成结算',
  'entrust:agent:job': '使用 Agent',
  'org:member:manage': '管理成员',
  'org:entrustment:manage': '管理授权'
}

/**
 * 「受理委托」权限码。
 *
 * 单独取一个常量，是因为它有一个**非展示**的用途（D-4 裁定 §2）：队列卡片与
 * 详情页拿它去本地的组织权限投影里查"我在**这张委托所属的**组织里能不能受理"。
 *
 * ⚠️ 上面那张 `ORG_PERMISSION_LABELS` 表**只做展示**，判定不得读它：
 *    标签与权限码是两种东西，读混了会让"改一次文案"变成"改一次判据"，
 *    而且改坏了**不报错** —— 只是按钮不再出现，看起来像权限不足。
 */
const ORG_PERM_CLAIM = 'entrust:assignment:claim'

/**
 * 「制作报价」权限码（= 后端 `access.PERM_QUOTE_CREATE`）。
 *
 * 与 `ORG_PERM_CLAIM` 同一条用法：详情页拿它去**本地组织权限投影**里查
 * "我在**这张委托所属的**组织里能不能组装对客报价"，只决定入口显不显示。
 * ⚠️ 判定不得读 `ORG_PERMISSION_LABELS`（那张表只做展示，理由见上）。
 */
const ORG_PERM_QUOTE_CREATE = 'entrust:quote:create'

/**
 * 「组织级读」权限码（= 后端 `access.PERM_VIEW`）。
 *
 * 运力候选 / 运力确认整组端点**只有组织侧一个通道**：`capacity_api` 里每一条都走
 * `assert_can_view_org`（读）或 `assert_can_write_entrustment`（写），
 * **没有**货主旁路 —— 候选行带承运人与供应商单价、确认行带 `agreed_amount`/`supplier`、
 * 逐规则判定里还有需求量与缺口吨数。⇒ 详情页拿它去本地组织权限投影里查
 * 「我能不能看这一块」，**只决定发不发这次取数**：客户侧一次请求都不发
 * （否则每次开页都发一个必然 403 的请求，而 403 被 `.catch` 吞成"没有数据"
 * 就成了一次静默降级 —— 那正是本页面反复写注释在防的那类缺陷）。
 *
 * ⚠️ 隐藏不等于放行：服务端仍按授权独立判定（同 `ORG_PERM_CLAIM` /
 *    `ORG_PERM_QUOTE_CREATE` 的用法）。
 */
const ORG_PERM_VIEW = 'entrust:view'

function statusLabel(status) {
  const meta = STATUS_META[status]
  return meta ? meta.label : '未知状态'
}

/**
 * 状态 → 标签样式类。
 *
 * 直接返回**全局样式类名**（app.wxss 里的 `chip` / `chip-warn` / `chip-muted`），
 * 而不是让 wxml 里拼 `'chip-' + tone` —— 拼串会把类的存在性检查推给运行时，
 * 写错一个 tone 值只会表现为"标签没颜色"，静态脚本抓不到。
 */
function statusClass(status) {
  const meta = STATUS_META[status]
  if (!meta) return 'chip chip-muted'
  if (meta.tone === 'warn') return 'chip chip-warn'
  if (meta.tone === 'primary') return 'chip chip-purple'
  return 'chip chip-muted'
}

/**
 * 入口可见性判定（AC-02）。
 *
 * 输入是探测结果 `{status, netError}`，输出 `{visible, reason, hint}`。
 * 关键规则：**只有服务端明确放行才显示**。网络不通、401、403、404 一律隐藏 ——
 * 显示一个"点进去必然失败"的入口，比不显示更糟：用户会以为是功能坏了，
 * 而不是"这个身份没有这项能力"。
 *
 * `400`（属于多个组织但未指定 org_id）算**可见**：他确实是经理，
 * 只是需要先选组织 —— 这不是权限问题，不该把入口藏掉。
 */
function entryDecision(res) {
  const status = (res && res.status) || 0
  if (res && res.netError) {
    return { visible: false, reason: 'offline', hint: '后端未连通，暂不显示委托入口' }
  }
  if (status === 200) return { visible: true, reason: 'ok', hint: '' }
  if (status === 400) return { visible: true, reason: 'need_org', hint: '请先选择服务经营主体' }
  if (status === 401) return { visible: false, reason: 'expired', hint: '登录已过期' }
  if (status === 403) return { visible: false, reason: 'denied', hint: '' }
  if (status === 404) return { visible: false, reason: 'disabled', hint: '' }
  return { visible: false, reason: 'unknown', hint: '' }
}

/**
 * 工作台渲染状态推导（AC-21）。
 *
 * 判定顺序不可调换，且**错误优先于空**：
 * 1. 还在加载 → loading（不看其它字段，避免"加载中闪一下空列表"）；
 * 2. 登录过期 → expired（要引导重新登录，不能显示成"没有委托"）；
 * 3. 无权限 → denied（要说清是权限问题，用户才知道去找谁开通）；
 * 4. 其它失败 → error（带可定位的提示）；
 * 5. 成功但 total=0 → empty；
 * 6. 否则 → ready。
 *
 * 把 `total` 而不是 `items.length` 作为"空"的依据：分页到第 3 页时
 * `items` 为空但 total 不为 0，那是"这一页没有数据"，不是"没有委托"。
 *
 * 为什么空态**文案**可以传进来、判定顺序不行：委托队列与案件队列要说的是两件事
 * （"还没有委托" vs "还没有异常或变更"），但"错误优先于空"是同一条铁律。
 * 于是只把措辞做成可选入参（`emptyTitle` / `emptyHint`），判定链**仍然只有这一条** ——
 * 给案件队列另写一个 `caseListState` 就等于承认两套顺序，而两套顺序迟早分叉。
 */
function viewState(input) {
  const state = input || {}
  if (state.loading) return { state: VIEW.LOADING, title: '加载中', hint: '' }
  const status = state.status || 0
  if (status === 401) {
    return { state: VIEW.EXPIRED, title: '登录已过期', hint: '请返回首页重新进入' }
  }
  if (status === 403) {
    return { state: VIEW.DENIED, title: '无查看权限', hint: '当前身份不在该组织内，或缺少「委托查看」权限' }
  }
  if (status === 400) {
    // 服务端说"属于多个组织，请用 org_id 指定"。这个状态**可由用户操作解决**：
    // 工作台顶部会显示组织选择器，选中即重取 —— 所以提示要指向"上方"，
    // 而不是让用户以为功能坏了。
    return { state: VIEW.DENIED, title: '需要选择服务经营主体', hint: '你属于多个组织，请在上方选择要查看的组织' }
  }
  if (status === 404) {
    return { state: VIEW.DENIED, title: '功能未开放', hint: '委托发货当前未启用' }
  }
  if (status && status !== 200) {
    return { state: VIEW.ERROR, title: '加载失败', hint: '接口返回 ' + status + (state.detail ? '：' + state.detail : '') }
  }
  if (state.netError) {
    return { state: VIEW.ERROR, title: '加载失败', hint: '无法连接后端，请确认服务已启动' }
  }
  if (!state.total) {
    return {
      state: VIEW.EMPTY,
      title: state.emptyTitle || '还没有委托',
      hint: state.emptyHint || '货主提交委托后会出现在这里'
    }
  }
  return { state: VIEW.READY, title: '', hint: '' }
}

/** 把接口载荷整理成模板直接可用的形状（模板不做事，避免 wxml 里写表达式）。 */
function decorateAssignment(row, permitted) {
  const data = row || {}
  const quantity = data.quantity === null || data.quantity === undefined ? '' : String(data.quantity)
  const unit = data.quantity_unit || ''
  const orgId = data.org_id === null || data.org_id === undefined ? '' : String(data.org_id)
  return {
    assignmentId: data.assignment_id,
    title: data.title || '未命名委托',
    cargoSummary: data.cargo_summary || '未填写货类',
    quantityText: quantity ? quantity + (unit ? ' ' + unit : '') : '货量未填写',
    status: data.status,
    statusLabel: statusLabel(data.status),
    statusClass: statusClass(data.status),
    // 队列卡片上「受理」入口是否出现 —— 与详情页**同一份判据**
    // （`canClaimAssignment`，D-4 裁定 §4：两个入口不得各写一份）。
    //
    // ⚠️ 第二参数是**该委托所属组织的权限投影**（`permittedOrgIds()` 的产出）。
    //    缺席（未传 / 权限还没加载 / 加载失败）⇒ 一律不显示：裁定 §4 要求
    //    "权限尚未加载或加载失败时，不提前展示可执行按钮"。保守方向是安全的 ——
    //    隐藏按钮**不等于**放行（写端 `claim_assignment` 仍按同一个 org_id 独立
    //    校验成员资格与权限），而猜成"有权限"会摆出一个必然被拒的按钮：
    //    用户点完只知道被拒，分不清是自己没权限还是单据有问题。
    canClaim: canClaimAssignment(data.status, orgId, permitted),
    revision: data.revision,
    createdAt: data.created_at || '',
    orgId: orgId,
    // 承接组织（S1 工作项 5「客户侧看到真实状态与承接组织」）。
    // `org_name` 为空**只有两种情况**：草稿还没选组织，或组织记录已不存在。
    // 两者对货主是同一件事 —— **还不知道谁会接手** —— 所以合成一句
    // 「尚未委托组织」，而不是把 null 渲染成空白（空白看起来像界面没渲染出来，
    // 而这恰恰是最需要说清楚的一格）。
    orgName: data.org_name || '',
    orgLabel: data.org_name || '尚未委托组织'
  }
}

// ⚠️ 必须显式包一层，**不能**写成 `rows.map(decorateAssignment)`：`map` 会把
// `(item, index, array)` 三个参数都传进去，于是 `index`（数字）被当成权限投影 ——
// 而 `isPermittedOrg(0, …)` 恒为 false ⇒ 表现是"受理按钮从来不出现"，
// 静默、且与真实权限无关。加第二参数的那一刻这行就从"无害"变成了"bug"。
function decorateList(rows, permitted) {
  return (rows || []).map(function (row) {
    return decorateAssignment(row, permitted)
  })
}

/** 分页提示：只反映当前页，不臆造总数文案（避免"共 N 条"与筛选条件脱节）。 */
function pageHint(total, page, size) {
  if (!total) return ''
  const pages = Math.max(1, Math.ceil(total / (size || 20)))
  return '第 ' + (page || 1) + '/' + pages + ' 页 · 共 ' + total + ' 条'
}

/**
 * 静默探测入口可见性。**不弹任何提示**（silent），
 * 因为"这个身份没有委托能力"是正常情况，不是错误。
 */
function probeEntry(orgId) {
  const data = { view: 'org', page: 1, size: 1 }
  if (orgId) data.org_id = orgId
  return request({ url: BASE + '/assignments', method: 'GET', data: data, silent: true })
    .then(function (res) {
      const decision = entryDecision({ status: 200 })
      decision.total = (res && res.total) || 0
      return decision
    })
    .catch(function (err) {
      const status = (err && err.httpStatus) || 0
      return entryDecision({ status: status, netError: !status })
    })
}

/**
 * 静默探测「我的委托」入口的可见性（AC-02 的**货主侧**）。
 *
 * 与 `probeEntry()` 的差别**不在实现，而在语义**：那一个探的是 `view=org`
 * （"我是不是某个组织里能受理委托的人"），这一个探的是 `view=owner`
 * （"委托这个能力对我开不开放"）。两个问题不同，答案也可以不同 ——
 * 一个账号完全可以"作为货主能提单、但不是任何组织的经理"。
 *
 * ⚠️ **空列表必须算可见**：`view=owner` 是恒可用的自有查询，`total=0` 只说明
 * "你还没提过委托"，那是**正常起点**，不是权限问题。把 0 当成不可见会让
 * **每一个新货主**都看不到入口 —— 这个功能在真实使用中等于不存在，
 * 而 CI 与真机走查都察觉不到（它们用的是有种子数据的账号）。
 * 判据因此只取 `entryDecision()` 的 status 分支，`total` 只作参考值返回。
 */
function probeOwnerEntry() {
  return request({
    url: BASE + '/assignments',
    method: 'GET',
    data: { view: 'owner', page: 1, size: 1 },
    silent: true
  })
    .then(function (res) {
      const decision = entryDecision({ status: 200 })
      decision.total = (res && res.total) || 0
      return decision
    })
    .catch(function (err) {
      const status = (err && err.httpStatus) || 0
      return entryDecision({ status: status, netError: !status })
    })
}

/** 拉取组织委托队列（经理工作台的数据源）。 */
function fetchQueue(options) {
  const opts = options || {}
  const data = { view: 'org', page: opts.page || 1, size: opts.size || 20 }
  if (opts.orgId) data.org_id = opts.orgId
  if (opts.status) data.status = opts.status
  return request({ url: BASE + '/assignments', method: 'GET', data: data })
}

/**
 * 拉取「我的委托」（货主视角 `view=owner`）。界面「我的委托」的数据源。
 *
 * ⚠️ 与 `fetchQueue()` 是**两个视角，不是两个筛选**：`view=org` 的范围是
 * "我所属组织的队列"（可见性由组织成员资格 + 授权 + 权限码判定），
 * `view=owner` 的范围是"我作为货主提交的单"。两者都不接受无范围查询 ——
 * 服务端在两者都给不出范围时返回空集，不做全表浏览。
 */
function fetchMine(options) {
  const opts = options || {}
  const data = { view: 'owner', page: opts.page || 1, size: opts.size || 20 }
  if (opts.status) data.status = opts.status
  return request({ url: BASE + '/assignments', method: 'GET', data: data })
}

/** 拉取单张委托（详情页）。可见性由服务端判定：非参与方 404。 */
function fetchAssignment(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId, method: 'GET' })
}

/**
 * 拉取「我所在的组织」清单（组织选择器的数据源）。
 *
 * 为什么必须有这个请求：`GET /assignments?view=org` 在「属于多个组织且未指定
 * org_id」时返回 400，而前端**无法自行枚举候选** —— `current_role` 存在本地
 * Storage、可被改写，且它表达的从来不是"我属于哪些组织"。没有它，多组织身份
 * 就是一个死局：服务端说"请指定组织"，界面却拿不出可选项。
 */
function fetchMyOrgs() {
  return request({ url: BASE + '/my-orgs', method: 'GET' })
}

/** 组织清单投影：模板不做事，且**不把 `member_role` 原样交给界面**（要译成中文）。 */
function decorateOrg(row) {
  const data = row || {}
  const labels = (data.permissions || []).map(function (p) {
    return ORG_PERMISSION_LABELS[p] || p
  })
  return {
    orgId: data.org_id === null || data.org_id === undefined ? '' : String(data.org_id),
    name: data.name || '未命名组织',
    roleLabel: ORG_ROLE_LABELS[data.member_role] || data.member_role || '成员',
    permissions: labels,
    permissionText: labels.join(' · ')
  }
}

function decorateOrgs(rows) {
  return (rows || []).map(decorateOrg)
}

/**
 * 从 `/my-orgs` 的**原始载荷**抽出「在这个组织里有 `perm` 权限」的集合（裁定 §1–§2）。
 *
 * 返回 `{ '3': true, '7': true }` —— 按 org_id 索引的表，不是数组也不是布尔：
 *
 *   · **按 org_id 索引**是硬要求。后端 `access.py` 记录过一次真实越权：把各组织的
 *     权限做**并集**后，"在 A 组织是经理、在 B 组织只是成员"的用户能认领 B 组织的
 *     委托。前端若做并集，就等于把那个**已经修掉的漏洞**在展示层重造一遍 ——
 *     而且是更坏的一种：界面说"你能受理"，服务端说"你不能"，用户无从理解。
 *   · **表而非布尔**：一次取数要能服务一整页（可能跨组织的）委托。
 *
 * ⚠️ 入参必须是**原始载荷**，不是 `decorateOrgs()` 的产出 —— 后者已把权限码翻成
 *    中文标签（`decorateOrg`），拿它查权限码**永远查不到**，且是静默的。
 *
 * 缺省（载荷缺失 / 加载失败）⇒ 空表 ⇒ 谁都不显示入口。这是**保守**方向：隐藏按钮
 * 不等于放行（写端仍独立校验），猜成"有权限"才会摆出必然被拒的按钮。
 */
function permittedOrgIds(rawOrgs, perm) {
  const map = {}
  ;(rawOrgs || []).forEach(function (row) {
    const data = row || {}
    const key = data.org_id === null || data.org_id === undefined ? '' : String(data.org_id)
    if (!key) return
    if ((data.permissions || []).indexOf(perm) >= 0) map[key] = true
  })
  return map
}

/** 在 `permittedOrgIds()` 的产出里查某个组织；`orgId` 先归一成字符串（两侧同型再比）。 */
function isPermittedOrg(permitted, orgId) {
  const key = orgId === null || orgId === undefined ? '' : String(orgId)
  return !!key && !!(permitted || {})[key]
}

/**
 * 「受理」入口可不可显示 —— 队列卡片与详情页**共用这一份判据**（D-4 裁定 §4）。
 *
 * 两个条件**必须同时**满足（裁定 §1）：
 *   1. 委托处于**可认领**状态（`submitted`）；
 *   2. 当前操作者在**该委托所属组织**内有 `entrust:assignment:claim`。
 *
 * 裁定 §1 同时禁掉两条捷径：不得只看委托状态，不得只看全局角色或"用户在别的组织
 * 里的权限"（后者正是 `access.py` 记的那个越权）。所以签名里必须带 `orgId`。
 *
 * 为什么不让两个调用方各写一遍 `status === 'submitted' && …`：写法一旦漂移，就会出现
 * "列表里能受理、点进去没有入口"（或反过来）这种自相矛盾的界面，而两边单看都对 ——
 * 这正是裁定 §4「统一判据」要消除的东西。
 *
 * ⚠️ 这里**不**判"当前用户是不是已经是该委托的负责人"之类的前置条件：认领是**建立**
 *    负责人关系的动作，要求"已经是负责人"会形成循环前置（裁定 §3）。
 * ⚠️ 这里**不是**权限判定。真正的判定在写端：`claim_assignment` 会按同一个 `org_id`
 *    再查一次 `ctx.can(PERM_ASSIGN_CLAIM, org_id=…)`。本函数只决定按钮显不显示 ——
 *    隐藏按钮**不能**代替服务端鉴权（裁定 §3）。
 */
function canClaimAssignment(status, orgId, permitted) {
  return status === 'submitted' && isPermittedOrg(permitted, orgId)
}

/**
 * 决定「当前该用哪个组织」。
 *
 * 规则顺序不可调换：
 * 1. 清单为空 → `none`（**没有任何组织身份**，界面要说清这是"没被加入组织"，
 *    而不是"没有数据"—— 两者的下一步动作完全不同）；
 * 2. 上次选的组织**仍在**清单里 → 用它（尊重选择，但必须仍然有效，
 *    否则用户会停在一个他已经不属于的组织上，看到的永远是空列表）；
 * 3. 只有一个组织 → 直接用它（不该让用户为唯一选项做一次选择）；
 * 4. 多个且没有有效记录 → `ambiguous`，**不猜**。
 *
 * 第 4 条是重点：猜错会让用户在毫无察觉的情况下看到**另一个组织**的队列 ——
 * 那比"请先选择"糟糕得多。看错组织的数据，用户几乎不可能自己发现。
 */
function pickOrg(orgs, savedOrgId) {
  const list = orgs || []
  const saved = savedOrgId === null || savedOrgId === undefined ? '' : String(savedOrgId)
  if (!list.length) return { orgId: '', reason: 'none' }
  if (saved) {
    const hit = list.filter(function (o) {
      return String(o.orgId) === saved
    })
    if (hit.length) return { orgId: String(hit[0].orgId), reason: 'saved' }
  }
  if (list.length === 1) return { orgId: String(list[0].orgId), reason: 'only' }
  return { orgId: '', reason: 'ambiguous' }
}

/**
 * 详情页字段投影（模板不做事）。
 *
 * **只投影货主自己填的字段**：受理价、成本口径、内部比价一律不出现在这里 ——
 * 经理侧的读写动作（受理、任务、成果、Agent）属于后续批次，本切片不做，
 * 也就不需要提前把这些内部字段拉出来。
 *
 * `permitted` 是组织权限投影（`permittedOrgIds()` 的产出），只用来算 `canClaim`
 * 这一个入口字段（D-4 裁定 §4）。**不传 ⇒ `canClaim` 恒 false**，那是保守缺省：
 * 详情页实际显示的受理入口取自工作台载荷（见 `detail.js`），这里保持一致语义，
 * 免得留下一个"看起来是判据、实际恒假"的字段把人读进沟里。
 */
function decorateDetail(row, permitted) {
  const data = row || {}
  const decorated = decorateAssignment(data, permitted)
  decorated.cargoSummary = data.cargo_summary || '未填写货类'
  decorated.quantityText = decorated.quantityText
  decorated.statusHint = STATUS_HINT[data.status] || ''
  decorated.createdAt = data.created_at || ''
  // 归属组织（S1 工作项 5）：优先用**组织名**。
  //
  // 此前这里只能给出 `组织 #7` —— 那不是设计，而是"后端没把名字交过来"的直接后果：
  // 货主看不懂一个编号，也不该被要求记住它。退回编号的分支**保留**（不是死代码），
  // 它覆盖真名确实取不到的情形（组织记录已不存在），而那时`未指定组织`会谎称
  // "没人接手"、编一个组织名更糟 —— **未知就说未知**。
  decorated.orgText = data.org_name
    ? data.org_name
    : decorated.orgId
      ? '组织 #' + decorated.orgId
      : '未指定组织'
  return decorated
}

// ─────────────────────────────────────────────────────────────────────────────
// 委托工作台（UI-05 / ENT-021）：七槽位骨架 + 空值四态
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 七槽位配置表 —— 前端这一侧的**顺序与 key 权威**（DR-0010 §3.1、§5）。
 *
 * §5 要求"槽位划分先在前端配置表落地"：改划分＝改这张表，不牵动数据库、不牵动状态机。
 * 接口按 `key` 返回数据，**渲染顺序由本表决定** —— 后端顺序错了界面仍然正确；
 * 而 `scripts/verify_entrust_ui.js` 会把"本表与后端 `workbench.SLOT_SPECS`
 * 不一致"直接判红（DR-0010 验证 #1）。两侧都有断言，就没人能单方面改顺序。
 *
 * `taskType` = 该槽位「记录任务」动作的默认任务类型（取值域 = 后端 `tasks.TASK_TYPES`）；
 * 留空即不提供该动作。`taskPick` 表示让用户先选类型 —— `plan_tasks` 本来就是
 * **全单任务总览**，不该替用户假定类型。
 *
 * `refKind` = 该槽位 `current.refs` 的**形状**（`REF_PROJECTORS` 的键）。**每个槽位都
 * 必须显式声明**：没有它取不到引用，而不是"退回成果形状"——见 `REF_PROJECTORS` 上
 * 的说明。`exceptions` 槽是唯一 `case`，其余是 `artifact`。
 */
const WORKBENCH_SLOTS = [
  { key: 'overview', title: '委托概况', taskType: '', refKind: 'artifact' },
  { key: 'plan_tasks', title: '方案与任务', taskType: '', taskPick: true, refKind: 'artifact' },
  { key: 'procurement', title: '采购与报价', taskType: 'purchase', refKind: 'artifact' },
  {
    key: 'customer_contracts',
    title: '对客方案与合同',
    taskType: 'contract',
    refKind: 'artifact'
  },
  { key: 'execution', title: '履约与交接', taskType: 'execution', refKind: 'artifact' },
  { key: 'exceptions', title: '异常与变更', taskType: '', refKind: 'case' },
  { key: 'settlement', title: '费用与结案', taskType: 'settlement', refKind: 'artifact' }
]

/** 任务类型 → 中文。键必须覆盖后端 `tasks.TASK_TYPES` 全部取值（静态断言守） */
const TASK_TYPE_LABELS = {
  collect_documents: '收集单证',
  quote: '询价',
  purchase: '采购',
  contract: '合同',
  execution: '履约',
  handover: '交接',
  settlement: '结算'
}

/** 任务类型的固定顺序（`plan_tasks` 的选择器按它排；与后端 `tasks.TASK_TYPES` 同集合） */
const TASK_TYPE_ORDER = [
  'collect_documents',
  'quote',
  'purchase',
  'contract',
  'execution',
  'handover',
  'settlement'
]

/**
 * 任务状态 → 中文（后端 `tasks.STATUS_*`）。
 *
 * 复核任务的状态要显示在案件页的复核清单上：只给一个 `pending`，读的人得回去翻
 * 任务页才知道那是什么意思；而"这批复核做完了没有"正是那张清单要回答的问题。
 * 静默漏一个键的后果是那一行只剩英文原值 —— `verify_entrust_ui.js` 对键集合有断言。
 */
const TASK_STATUS_LABELS = {
  pending: '待开始',
  in_progress: '进行中',
  waiting: '等待中',
  done: '已完成',
  cancelled: '已取消'
}

/**
 * 空值四态文案（DR-0010 §3.6）—— **四句话必须不同**。
 *
 * 「暂无记录 / 尚未分配 / 不适用 / 信息缺失」各自回答不同的问题：
 * 前两个是"还没发生"，第三个是"本单不需要"，第四个是"该有却没有"。
 * 一律显示「待补充」的后果是用户分不清**该不该动手** —— 而这正是四态存在的理由。
 * 「本期未开放」不属于四态：它是能力没做（§3.8），必须与"业务上没有"分开。
 */
const SLOT_EMPTY_TEXT = {
  noRecord: '暂无记录',
  unassigned: '尚未分配',
  notApplicable: '不适用',
  missingInfo: '信息缺失',
  notOpen: '本期未开放'
}

/** 派生字段的固定标签与顺序（§3.5；顺序即展示顺序） */
const SLOT_FIELD_LABELS = ['当前成果', '未决问题', '下一责任方', '最后更新']

/**
 * 未决问题的**类别**标签（后端 `WorkbenchIssueOut.kind`）。
 *
 * 类别由服务端给，界面**不靠描述文本的前缀去猜** —— 猜的写法一旦后端改了措辞就静默
 * 失效，而且"缺项"和"阻断"对用户的意义完全不同：前者是去补数据，后者是这条路走不通。
 * 少一个键 → 该类别会退化成只显示描述文本（`verify_entrust_ui.js` 会核对两侧取值域）。
 */
const ISSUE_KIND_LABELS = {
  missing_field: '缺项',
  blocked: '阻断',
  waiting: '待确认',
  unassigned_task: '未指派',
  inactive_artifact: '失效成果'
}

function _slotField(label, value, empty, tone) {
  // 样式类在这里算好：模板里写 `{{a ? 'x' : 'y'}}` 会让"类是否存在"变成运行期才知道，
  // 而样式类拼错只会表现为"没有颜色"，静态脚本抓不到（verify_entrust_ui.js 有专门检查）。
  let cls = 'field-value'
  if (empty) cls += ' slot-empty'
  else if (tone === 'warn') cls += ' slot-warn'
  return { label: label, value: value, empty: !!empty, tone: tone || '', cls: cls }
}

/** 「当前成果」行 */
function _currentField(slot) {
  const current = slot.current || {}
  const text = current.state === 'present' ? current.text || '' : ''
  return _slotField(
    SLOT_FIELD_LABELS[0],
    text || SLOT_EMPTY_TEXT.noRecord,
    !text
  )
}

/** 「未决问题」行 —— 「信息缺失」是一种**有内容的**状态，不是空 */
function _issuesField(slot) {
  const issues = slot.issues || {}
  const count = issues.count || 0
  if (issues.state === 'missing_info') {
    return _slotField(SLOT_FIELD_LABELS[1], SLOT_EMPTY_TEXT.missingInfo + ' · ' + count + ' 项', false, 'warn')
  }
  if (issues.state === 'present') {
    return _slotField(SLOT_FIELD_LABELS[1], '未决 ' + count + ' 项', false, 'warn')
  }
  return _slotField(SLOT_FIELD_LABELS[1], '无未决问题', true)
}

/** 「下一责任方」行 —— 「尚未分配」与「不适用」是两件事 */
function _ownerField(slot) {
  const owner = slot.next_owner || {}
  if (owner.state === 'assigned') {
    const text = owner.text || (owner.user_id ? '成员 #' + owner.user_id : '已指派')
    return _slotField(SLOT_FIELD_LABELS[2], text, false)
  }
  if (owner.state === 'unassigned') {
    return _slotField(SLOT_FIELD_LABELS[2], SLOT_EMPTY_TEXT.unassigned, true, 'warn')
  }
  return _slotField(SLOT_FIELD_LABELS[2], SLOT_EMPTY_TEXT.notApplicable, true)
}

/** 「最后更新」行 */
function _updatedField(slot) {
  const at = slot.updated_at || ''
  return _slotField(SLOT_FIELD_LABELS[3], at || SLOT_EMPTY_TEXT.noRecord, !at)
}

/** 计数摘要（只写有内容的项，避免出现「任务 0 · 成果 0」这种噪声） */
function _countsText(slot) {
  const counts = slot.counts || {}
  const parts = []
  if (counts.tasks) {
    const open = counts.open_tasks || 0
    parts.push('任务 ' + counts.tasks + (open ? '（未完成 ' + open + '）' : ''))
  }
  if (counts.artifacts) parts.push('成果 ' + counts.artifacts)
  return parts.join(' · ')
}

/** 引用里的 ID 缺了就写 `—`：`'#' + undefined` 会渲染成 `#undefined`（未知被装扮成已知） */
function _refId(id) {
  return id === null || id === undefined || id === '' ? '—' : id
}

/** 只连接有的项 —— 缺一项就少一个分隔符，不留 `a ·  · b` 这种悬空分隔符 */
function _joinParts(parts) {
  return parts
    .filter(function (p) {
      return p !== null && p !== undefined && p !== ''
    })
    .join(' · ')
}

/** 成果精确版本引用（PRD 第 187 行：对话与工作台引用**同一** artifact ID 与版本） */
function _artifactRefs(slot) {
  const current = slot.current || {}
  return (current.refs || []).map(function (ref) {
    const version = ref.revision_no === null || ref.revision_no === undefined ? '—' : ref.revision_no
    return {
      kind: 'artifact',
      id: ref.artifact_id,
      cls: '',
      // 整行文案在投影层拼好，模板只做 `{{rf.text}}`：
      // 「模板只做 wx:for，不做表达式」是这一层的既有约定，好处是未知值
      // （缺 ID / 缺版本）的处理只有一处，不在 wxml 里判第二次。
      text: _joinParts([
        '成果 #' + _refId(ref.artifact_id),
        ref.label || ref.artifact_type,
        'v' + version
      ])
    }
  })
}

/**
 * 案件引用（UI-05 `exceptions` 槽；DR-0014 §3.3 的 `CaseRef`）。
 *
 * **必须是独立的一个投影器**，不能与 `_artifactRefs` 合一：
 *
 *   · 键不同 —— 案件是 `case_id` / `title` / `kind` / `status` / `blocking`；
 *   · **没有「第几版」** —— 案件的 `revision_no` 是乐观锁版本号，不是"看哪一版"。
 *     把两个形状喂给同一个映射，会在某次改动里静默错位，症状是槽位渲染出
 *     `undefined · v—`，且 `data-id` 为空、**点了没反应**；
 *   · 深链参数也不同（成果页要 `artifact_id`、案件页要 `case_id`）。
 *
 * 阻断标记写进文案而不是只给一个类名：它是"为什么要现在看这宗案子"的答案，
 * 只在样式里表达（颜色）会让不点进去的人永远不知道。
 */
function _caseRefs(slot) {
  const current = slot.current || {}
  return (current.refs || []).map(function (ref) {
    return {
      kind: 'case',
      id: ref.case_id,
      cls: ref.blocking ? 'slot-ref-block' : '',
      text: _joinParts([
        '案件 #' + _refId(ref.case_id),
        ref.title,
        CASE_KIND_LABELS[ref.kind],
        CASE_STATUS_LABELS[ref.status],
        ref.blocking ? '阻断执行' : ''
      ])
    }
  })
}

/**
 * 槽位引用投影器表 —— `decorateSlot` 按槽位配置的 `refKind` 取，**不靠猜字段**。
 *
 * 后端 `current.refs` 是联合类型（`WorkbenchArtifactRef | CaseRef`，`schemas.py`）。
 * 取不到投影器时给空数组**而不是退回成果形状**：退回就是把"这个槽位的引用格式
 * 还没对齐"装扮成"这个槽位没有引用"，而后者是一句业务结论。
 */
const REF_PROJECTORS = {
  artifact: _artifactRefs,
  case: _caseRefs
}

/** 按槽位配置取引用投影器；未知 `refKind` 给空数组（理由见 `REF_PROJECTORS` 注释） */
function _refsFor(config, slot) {
  const project = REF_PROJECTORS[config.refKind]
  return typeof project === 'function' ? project(slot) : []
}

/**
 * 单槽位投影。**未开放 ≠ 空**：`available=false` 走独立分支，给「本期未开放」，
 * 绝不落进四态 —— 否则「异常与变更本期没做」会被说成「这单没有异常」（DR-0010 §3.8）。
 *
 * 接口没返回该槽位时（版本不匹配 / 后端漏了一个 key）也走未开放分支，但**理由不同**：
 * 文案如实说"接口未返回该槽位"，而不是伪造一个正常的业务空态。
 */
function decorateSlot(config, raw) {
  const slot = raw || {}
  const available = !!raw && slot.available !== false
  if (!available) {
    return {
      key: config.key,
      title: config.title,
      available: false,
      taskType: '',
      taskPick: false,
      tag: SLOT_EMPTY_TEXT.notOpen,
      tagClass: 'chip chip-muted',
      note: slot.unavailable_reason || '接口未返回该槽位（后端版本可能不匹配）',
      fields: [],
      issues: [],
      countsText: '',
      refs: [],
      canRecordTask: false,
      actionLabel: ''
    }
  }
  const issues = slot.issues || {}
  return {
    key: config.key,
    title: config.title,
    available: true,
    // 动作参数进模板 dataset：页面不必再查一遍配置表（少一份会漂移的映射）
    taskType: config.taskType || '',
    taskPick: !!config.taskPick,
    tag: '',
    tagClass: '',
    note: '',
    fields: [_currentField(slot), _issuesField(slot), _ownerField(slot), _updatedField(slot)],
    issues: (issues.items || []).map(function (item) {
      // 逐条同时给出类别标签与描述：只给描述时，用户得先读懂一句话才知道该不该动手
      return {
        kind: item.kind || '',
        kindLabel: ISSUE_KIND_LABELS[item.kind] || '',
        text: item.text
      }
    }),
    countsText: _countsText(slot),
    refs: _refsFor(config, slot),
    canRecordTask: !!(config.taskType || config.taskPick),
    actionLabel: config.taskPick ? '新建任务' : config.taskType ? '记录任务' : ''
  }
}

/**
 * 工作台载荷投影：接口 → 模板形状（模板只做 wx:for，不做表达式）。
 *
 * 遍历的是**前端配置表**而不是接口数组：接口按 key 提供数据，顺序由本表决定。
 * 接口多返回未知 key 时忽略（不渲染一个配置表没声明的槽位 —— 那说明两侧不同步，
 * 静态断言会红，界面上不该悄悄多出一块没人认识的东西）。
 */
function decorateWorkbench(res) {
  const data = res || {}
  const byKey = {}
  ;(data.slots || []).forEach(function (slot) {
    if (slot && slot.key) byKey[slot.key] = slot
  })
  const unassigned = Number(data.unassigned_artifact_total || 0)
  return {
    assignmentId: data.assignment_id,
    orgId: data.org_id === null || data.org_id === undefined ? '' : String(data.org_id),
    status: data.status,
    statusLabel: statusLabel(data.status),
    statusClass: statusClass(data.status),
    slots: WORKBENCH_SLOTS.map(function (config) {
      return decorateSlot(config, byKey[config.key])
    }),
    unassignedTotal: unassigned,
    // 历史成果必须如实报数，不能因为"不属于任何槽位"就消失
    unassignedHint: unassigned
      ? '另有 ' + unassigned + ' 份历史成果尚未归属（归属机制上线前产生），不在上述槽位内'
      : ''
  }
}

/** 幂等键：写端点要求，重试同一次操作时复用同一个键 */
function newIdempotencyKey(prefix) {
  return (
    (prefix || 'k') + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
  )
}

/** 拉取单委托工作台（七槽位摘要）。可见性由服务端判定：非参与方 404。 */
function fetchWorkbench(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId + '/workbench', method: 'GET' })
}

/**
 * 在工作台里**记录一项任务**（UI-05 首片的人工落点）。
 *
 * 用既有端点而不是为工作台新开一个写口：任务模型已经承载了"派单 / 前置 / 证据"，
 * 工作台只是它的一个入口。`Idempotency-Key` 由调用方生成并在重试时复用，
 * 否则一次网络抖动会留下两条一模一样的任务。
 */
function createTask(assignmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/tasks',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 受理委托（货主已提交、经理认领）。原子认领，双认领后者 409。 */
function claimAssignment(assignmentId, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/claim',
    method: 'POST',
    data: {},
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// 成果的编辑与确认（UI-05 第二片 / ENT-023）
// ─────────────────────────────────────────────────────────────────────────────
//
// ── 本片补的是什么 ────────────────────────────────────────────────────
// 首片（ENT-021）把七个槽位和「当前成果 · vN」显示出来了，但成果是**只读**的：
// §3.8 要求"范围内槽位必须有可用人工操作（编辑、记录、确认）"，成果这一侧当时
// 缺的正是编辑与确认。所以本片不是新造机制 —— 后端 `POST /artifacts/{aid}/revisions`
// 与 `/confirm` 早已存在且测试扎实，缺的是**人与确认的落点**。
//
// ── 三条不可含糊的语义（写在代码里，不靠注释口头保证）────────────────
//
// 1. **编辑不改变生效版本**。`append_revision` 只追加，生效版本只能被显式确认
//    改变。界面必须把这件事**说出来**：保存成功后提示「已保存为 vN，生效版本仍是 vM」。
//    只说"保存成功"会让用户以为客户看到的已经是新内容 —— 这是最坏的一种误导。
//
// 2. **确认绑定的是精确版本号，不是"最新"**。确认卡上必须显示
//    `target artifact #N · vK`（PRD 第 187/188 行：对话与工作台引用同一 ID 与版本）。
//    允许把生效版本**回退**到更早的历史版本 —— 后端如此设计（可以确认 v1），
//    界面不得擅自禁止（禁止了就与后端语义分叉，而分叉的那一侧将来一定不同步）。
//
// 3. **编辑不得静默丢弃任何字段**。表单从**当前生效版本的 payload** 出发做增量修改，
//    而不是从表单字段重新拼一份：这样未在注册表里声明的历史字段（`unknown_fields`）
//    会原样保留。用"按表单重建"的写法，一次无害的编辑就会把 Agent 早期产出的
//    额外字段悄悄抹掉，而且没有任何人会察觉。
//
// ── 为什么不引入成果自己的状态机 ──────────────────────────────────────
// PRD 第 275 行描述的是 `draft / in_review / confirmed / superseded` 加独立的
// 共享/接受/执行/证据状态，后端目前只有 `ent_artifact.status`(active/void) 与
// `current_revision_id`。本片**不假装**已有那套状态机：界面上出现的每一个标签
// 都是从既有事实**派生**的（生效/历史 = 是否等于 `current_revision_id`；
// 来源 = revision 的 `source`），没有一个是凭空写死的枚举。

/**
 * 版本角色标签 —— **派生**，不是存储的状态。
 *
 * 「生效版本」= `current_revision_id` 指向的那一条；其余都是历史版本。
 * 不引入 revision 级 status 字段，是为了不与后端语义分叉：
 * 一旦前端自己记"哪个是最新的"，两边就会在某个编辑/确认交错后不一致。
 */
const REVISION_ROLE = { current: '生效版本', superseded: '历史版本' }

/** 成果可用性标签。键与后端 `artifacts.STATUS_*` 逐字对应（静态脚本交叉断言）。 */
const ARTIFACT_STATUS_LABELS = { active: '有效', void: '已作废' }

/**
 * 版本来源标签。键与后端 `artifacts.SOURCE_*` 逐字对应。
 * 中文不写「手工」而写「人工」：它要与「人工接管优先」这条机制同名，
 * 用户看到「人工」才能对应上"我改过之后 Agent 不会再覆盖"。
 */
const REVISION_SOURCE_LABELS = { manual: '人工', agent: 'Agent' }

/**
 * 成果字段中文标签。键必须**覆盖后端 `registry.py` 全部 required + optional 字段**
 * （由 `scripts/verify_entrust_ui.js` 交叉断言，少一个即红）。
 *
 * 回退值是字段名本身而不是空串：未知字段（历史数据或注册表新增但前端没跟）
 * 会显示成 `receivable_lines` 这样的原始键 —— 难看，但**看得见**。
 * 回退成空串会让整行变成一个空白标签，那才是真的没人能发现出了问题。
 */
const ARTIFACT_FIELD_LABELS = {
  carrier: '承运方',
  rate: '报价单价',
  rate_unit: '计价单位',
  cargo_name: '货名',
  quantity: '数量',
  quantity_unit: '数量单位',
  route: '航线 / 区间',
  valid_until: '有效期至',
  candidates: '候选方案',
  selected_candidate: '已选方案',
  comparison_note: '比价说明',
  amount: '报价金额',
  currency: '币种',
  includes: '包含项',
  excludes: '不含项',
  note: '备注',
  parties: '合同当事方',
  clauses: '条款清单',
  effective_date: '生效日期',
  supplier: '供应商',
  agreed_scope: '约定范围',
  agreed_amount: '约定金额',
  effective_from: '起始生效时间',
  receivable_lines: '应收明细',
  payable_lines: '应付明细',
  disputed: '争议项'
}

function artifactFieldLabel(name) {
  return ARTIFACT_FIELD_LABELS[name] || name
}

function artifactStatusLabel(status) {
  return ARTIFACT_STATUS_LABELS[status] || '未知状态'
}

/**
 * 成果状态 → 全局样式类（app.wxss 里的 `chip*`）。
 *
 * 刻意**不用** `chip-success`（绿）表示"有效"：绿色的语义是"这件事成功了"，
 * 而"有效"只是"它还没被作废"。绿色会让一个还没被确认过的成果看起来像已经办妥
 * —— 与 PRD 第 183 行"不要渲染静态成功徽标"是同一条理由。
 */
function artifactStatusClass(status) {
  if (status === 'active') return 'chip chip-purple'
  return 'chip chip-muted'
}

function revisionSourceLabel(source) {
  return REVISION_SOURCE_LABELS[source] || source || '未知来源'
}

/**
 * 标量值的**显示文本**。结构化值（列表 / 对象）走 JSON，见 `_fieldKind`。
 * `null` / 缺值一律显示空串 —— 空就是空，不显示 "null" 让人以为填了个值。
 */
function _scalarText(raw) {
  if (raw === null || raw === undefined) return ''
  if (typeof raw === 'boolean') return raw ? 'true' : 'false'
  return String(raw)
}

/**
 * 结构化值（列表 / 对象）的**显示文本**。
 *
 * 与 `_scalarText` 同一口径：**空就是空**。缺值时返回空串而不是
 * `JSON.stringify(null)` 得到的 `"null"` —— 后者会让一个从没填过的字段
 * 显示成有内容的 `null`，用户以为那里已经填了东西。
 *
 * `undefined` 尤其要挡住：`JSON.stringify(undefined)` 返回的是 **JS undefined
 * 本身**（不是字符串），setData 会把这个键丢掉，输入框拿到 `undefined` 表现为
 * 一片空白 —— 看起来"没问题"，实际是数据层少了一个键，模板判断都会走偏。
 */
function _structuredText(raw) {
  if (raw === null || raw === undefined || raw === '') return ''
  return JSON.stringify(raw, null, 2)
}

/**
 * 后端 `registry.py` 的**字段类型取值域**（键与 `FIELD_TEXT/NUMBER/LIST/OBJECT`
 * 逐字对应，由 `scripts/verify_entrust_ui.js` 跨语言断言）。
 *
 * 值写的是**前端视角的编辑形态**，不是类型的复述：`text` / `number` 都走单行
 * 输入，只有 `list` / `object` 需要 JSON 编辑器。这样前端只判一次，不必在每个
 * 分支里重申"number 也是标量"。
 */
const ARTIFACT_FIELD_KINDS = { text: '标量', number: '标量', list: '列表', object: '结构化' }

/** 需要 JSON 多行编辑器的**声明类型** */
const STRUCTURED_FIELD_KINDS = { list: true, object: true }

/**
 * 字段的编辑形态：标量走单行输入，结构化（列表/对象）走 JSON 多行文本。
 *
 * **契约优先，值推断只作回退** —— 第二个参数是后端注册表声明的类型
 * （`field_types[name]`）。为什么不能只看值：**缺值字段没有值可看**。
 * `settlement_draft.receivable_lines` 在刚创建时是空的（键可能都不存在），
 * 按值推断会判成标量、渲染成单行输入框，用户填 `[1,2]` 存回去得到的是
 * **字符串** `"[1,2]"` —— 内容看着对、类型错了，缺项判定与客户投影会一起错，
 * 而界面上完全看不出异常。
 *
 * 回退（`declared` 为空串）**只对未知字段成立**：它们没有契约，按值推断是唯一
 * 可得的信息，且本来就是只读的。
 */
function _fieldKind(raw, declared) {
  if (declared === 'list' || declared === 'object') return 'json'
  if (declared === 'text' || declared === 'number') return 'scalar'
  if (Array.isArray(raw) || (raw !== null && typeof raw === 'object')) return 'json'
  return 'scalar'
}

/**
 * 字段类型的**界面提示**（编辑态显示在输入框上方）。
 *
 * 只对结构化类型给提示。用户可以接受"这里要填 JSON"，但**不可能**知道
 * "这个框本该是列表，只是它现在空着所以看起来像文本框" —— 后者要靠提示说清楚。
 */
function fieldKindHint(declared) {
  if (declared === 'list') return '列表（JSON 数组）'
  if (declared === 'object') return '结构化（JSON 对象）'
  return ''
}

/**
 * 把编辑框文本**按原值（或契约声明）的类型**还原。
 *
 * 为什么需要它：表单里一切都是字符串，若原值 `rate` 是数字 12，用户没动它，
 * 存回去就变成 `"12"` —— 内容看着没变，**类型却变了**，而下游（客户投影、
 * 金额比对）对类型是敏感的。规则：
 *   · 文本与原显示文本一致 → **原值原样返回**（彻底避免"没改也被改"）；
 *   · 契约声明为数字 → 文本仍是数字就还原成数字（**原值为空时也成立**：
 *     新填的字段没有"原值类型"可看，只能靠契约）；
 *   · 原来是数字且文本仍是数字 → 还原成数字；
 *   · 原来是布尔 → 'true'/'false' 还原成布尔；
 *   · 其余 → 文本本身。
 *
 * 为什么契约要**排在原值类型之前**：原值为空（`null`）是常态 —— 草稿允许不完整。
 * 只看原值时，用户第一次填 `rate` 会存成字符串；而同一个字段第二次编辑
 * 又会还原成数字。**同一个字段的两次编辑产生不同类型的值**，是最难查的一类不一致。
 */
function coerceLike(raw, text, declared) {
  const s = text === null || text === undefined ? '' : String(text)
  if (s === _scalarText(raw)) return raw
  if (declared === 'number') {
    return /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : s
  }
  if (typeof raw === 'number') {
    if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s)
    return s
  }
  if (typeof raw === 'boolean') return s === 'true'
  return s
}

/**
 * 成果详情投影。
 *
 * `fields` 的顺序 = 注册表声明的顺序（必填在前、选填在后），**不由 payload 的键序决定**：
 * 键序随 JSON 序列化实现变化，界面顺序会莫名其妙地抖。
 * 未在注册表声明的字段（`unknown_fields`）追加在最后并标 `unknown: true` ——
 * 它们**只读**，但会随编辑一起原样保存（见 `buildPayload`）。
 */
function decorateArtifact(artifact, spec) {
  const data = artifact || {}
  const s = spec || {}
  const current = data.current_revision || {}
  const payload = current.payload || {}
  const required = s.required_fields || []
  const optional = s.optional_fields || []
  const missing = data.missing_fields || []
  const unknown = data.unknown_fields || []
  const internal = s.internal_fields || []
  const fieldTypes = s.field_types || {}
  const status = data.status
  // 待复核标记（五之四投影）：`undefined`/`null` 都归一成 `null` = 没有待复核项。
  const mark = data.needs_revalidation || null

  function row(name, isRequired, declared) {
    const raw = payload[name]
    const kind = _fieldKind(raw, declared)
    return {
      name: name,
      label: artifactFieldLabel(name),
      required: !!isRequired,
      internal: internal.indexOf(name) !== -1,
      unknown: false,
      // 注册表声明的类型（空串＝未声明）。模板据此显示"列表（JSON 数组）"提示，
      // 也让静态断言能核对"声明有没有一路传到渲染层"。
      declared: declared || '',
      kindHint: fieldKindHint(declared),
      kind: kind,
      value: kind === 'json' ? _structuredText(raw) : _scalarText(raw),
      raw: raw === undefined ? null : raw,
      empty: raw === null || raw === undefined || raw === ''
    }
  }

  const fields = []
  required.forEach(function (n) {
    fields.push(row(n, true, fieldTypes[n]))
  })
  optional.forEach(function (n) {
    if (required.indexOf(n) === -1) fields.push(row(n, false, fieldTypes[n]))
  })
  unknown.forEach(function (n) {
    const raw = payload[n]
    // 未知字段**没有契约**（注册表没声明它）：只能按值推断，且它本来就是只读的
    const kind = _fieldKind(raw, '')
    fields.push({
      name: n,
      label: artifactFieldLabel(n),
      required: false,
      internal: false,
      unknown: true,
      declared: '',
      kindHint: '',
      kind: kind,
      // 未知字段只读：它没有字段契约，做表单等于替 Agent 猜语义
      value: kind === 'json' ? _structuredText(raw) : _scalarText(raw),
      raw: raw === undefined ? null : raw,
      empty: false
    })
  })

  return {
    artifactId: data.artifact_id,
    entrustmentId: data.entrustment_id,
    assignmentId: data.assignment_id === null || data.assignment_id === undefined
      ? ''
      : String(data.assignment_id),
    typeCode: data.artifact_type,
    typeLabel: s.label || data.artifact_type,
    status: status,
    statusLabel: artifactStatusLabel(status),
    statusClass: artifactStatusClass(status),
    voided: status === 'void',
    currentRevisionId: data.current_revision_id,
    // 版本号有两个来源，**顺序不能反**：
    //  · 详情端点（`GET /artifacts/{id}`）把版本嵌在 `current_revision` 里；
    //  · 单委托清单（`GET /assignments/{id}/artifacts`）给的是**扁平**的
    //    `current_revision_no` —— 后端注释写明这一对值就是"工作台与聊天卡引用精确版本"的依据。
    // 这里做一次归一，是为了让**同一份成果在两个端点上拿到同一个版本号**；
    // 若只认嵌套字段，会话卡会显示成空（`v` 后面什么都没有），而"没版本号"与
    // "版本号是空的"在界面上分不出来 —— AC-05 要的正是这一行字。
    currentRevisionNo: current.revision_no === undefined
      ? (data.current_revision_no === undefined || data.current_revision_no === null
        ? null
        : data.current_revision_no)
      : current.revision_no,
    currentNote: current.note || '',
    currentCreatedAt: current.created_at || '',
    updatedAt: data.updated_at || '',
    fields: fields,
    missingFields: missing,
    missingLabels: missing.map(artifactFieldLabel),
    // 「缺项」必须点名到字段：只说"信息不完整"，用户不知道该去补哪一个
    missingHint: missing.length
      ? '还缺 ' + missing.length + ' 项必填：' + missing.map(artifactFieldLabel).join('、')
      : '',
    unknownFields: unknown,
    unknownHint: unknown.length
      ? '另有 ' + unknown.length + ' 个未在注册表中声明的字段（' + unknown.join('、') + '），只读且会原样保留'
      : '',
    // 注册表的 editable 是**声明**（R1 全为真，保留给"只读计算类成果"）；
    // 这里消费它，避免出现"注册表声明了但没人看"的字段。
    //
    // `registryKnown` 一并参与判定：类型不在注册表里时后端 `append_revision` 必然
    // 400（字段契约无从校验），前端就不该先把编辑入口亮出来让人白填一遍。
    registryKnown: !!s.code,
    canEdit: status === 'active' && !!s.code && s.editable !== false,
    canConfirm: status === 'active',
    // 是否可确认**取决于选中的版本**，由页面在拿到选中项后填；这里只给状态前提
    statusHint: status === 'void' ? '已作废的成果不能再编辑或确认（历史版本仍可审计）' : '',
    registryHint: s.code ? '' : '该成果类型不在当前注册表中，字段契约无从校验：只读',
    // ── 待复核标记（A2 五之二派生、五之四处投影）──────────────────────────
    // `null` = 没有待复核项。**不把它归一成空对象**：`null` 与 `{}` 在模板里
    // 一个走"没有徽标"、一个走"有徽标但区域为空"，而后者会渲染出一个莫名的空徽标。
    //
    // ⚠️ 徽标只说明"这一版**依赖的**事实变了"，**不等于**成果本身错了：
    //    文案必须留出这个区别，否则看的人会以为内容已经失效、可以不管了。
    needsRevalidation: mark
      ? {
          count: mark.count || 0,
          areasText: (mark.areas || []).join('、'),
          caseIds: mark.case_ids || [],
          caseIdsText: (mark.case_ids || []).map(function (id) {
            return '#' + id
          }).join('、'),
          reviewTaskIds: mark.review_task_ids || [],
          // 模板里不能调 `.join()`（WXML 表达式受限），所以在这里拼好。
          // 空数组给 '—'：显示成空串会让那一行看起来像排版漏了内容。
          reviewTaskIdsText: (mark.review_task_ids || []).length
            ? (mark.review_task_ids || [])
                .map(function (id) {
                  return '#' + id
                })
                .join('、')
            : '—',
          // 确认按钮的**前置**：有待复核项时后端会 409（AC-12 后半条）。
          // 这里只是把话说在前面 —— 真正的拒绝在写端，界面不做权限判断。
          confirmBlockedHint: '该成果有未完成的复核项，设为生效版本会被拒绝：请先完成复核任务'
        }
      : null
  }
}

/**
 * 版本历史投影。`currentRevisionId` 决定每条的「生效 / 历史」角色 ——
 * 派生自后端事实，不由前端另记一份。
 */
function decorateRevisions(items, currentRevisionId) {
  return (items || []).map(function (r) {
    const isCurrent =
      currentRevisionId !== null &&
      currentRevisionId !== undefined &&
      String(r.revision_id) === String(currentRevisionId)
    return {
      revisionId: r.revision_id,
      revisionNo: r.revision_no,
      isCurrent: isCurrent,
      roleLabel: isCurrent ? REVISION_ROLE.current : REVISION_ROLE.superseded,
      roleClass: isCurrent ? 'chip chip-purple' : 'chip chip-muted',
      source: r.source || '',
      sourceLabel: revisionSourceLabel(r.source),
      note: r.note || '',
      createdAt: r.created_at || '',
      title: 'v' + r.revision_no + ' · ' + revisionSourceLabel(r.source),
      summary: (r.note || '').trim() || '（无备注）'
    }
  })
}

/**
 * 由编辑表单构建提交用的 payload。**从当前 payload 出发做增量修改**。
 *
 * 这条决定不是实现细节，而是上面第 3 条语义的落点：任何"按表单字段重建 payload"
 * 的写法都会丢掉注册表未声明的键（`unknown_fields`）—— 那是一次静默的数据丢失。
 *
 * @param {object} basePayload 当前生效版本的 payload（原样拷贝的起点）
 * @param {Array} fields       decorateArtifact().fields（含 unknown）
 * @param {object} drafts      { 字段名: 编辑框文本 }
 * @returns {{ok:boolean, payload:object, errors:Array<{name:string,reason:string}>}}
 */
function buildPayload(basePayload, fields, drafts) {
  const payload = {}
  const base = basePayload || {}
  // 先整体拷一份：未参与表单的键（历史上出现过、后来从注册表移除的）不该被这次编辑抹掉
  Object.keys(base).forEach(function (k) {
    payload[k] = base[k]
  })

  const d = drafts || {}
  const errors = []
  ;(fields || []).forEach(function (f) {
    if (f.unknown) return // 只读字段：原样保留，不参与本次修改
    const text = d[f.name] === undefined || d[f.name] === null ? '' : String(d[f.name])
    if (f.kind === 'json') {
      if (text.trim() === '') {
        // 清空结构化字段＝移除它。**不是**写成空对象：空对象在缺项判定里非空，
        // 会把"我没填"变成"填了个空壳"，而下游据此以为这栏已经办好了。
        delete payload[f.name]
        return
      }
      try {
        payload[f.name] = JSON.parse(text)
      } catch (e) {
        errors.push({ name: f.name, reason: '不是合法的 JSON' })
      }
      return
    }
    if (text === '') {
      delete payload[f.name]
      return
    }
    payload[f.name] = coerceLike(f.raw, text, f.declared)
  })

  return {
    ok: errors.length === 0,
    payload: payload,
    errors: errors,
    errorHint: errors.length
      ? errors.map(function (e) { return artifactFieldLabel(e.name) + '：' + e.reason }).join('；')
      : ''
  }
}

/** 原始字段值 → 编辑框初始文本（与 decorateArtifact 的 `value` 同口径） */
function fieldDrafts(fields) {
  const out = {}
  ;(fields || []).forEach(function (f) {
    out[f.name] = f.value
  })
  return out
}

/** 表单是否有未保存改动（与初始文本逐字段比对；只比可编辑字段） */
function isArtifactDirty(fields, drafts, initial) {
  const d = drafts || {}
  const init = initial || {}
  return (fields || []).some(function (f) {
    if (f.unknown) return false
    const a = d[f.name] === undefined || d[f.name] === null ? '' : String(d[f.name])
    const b = init[f.name] === undefined || init[f.name] === null ? '' : String(init[f.name])
    return a !== b
  })
}

/**
 * 确认前的「确认卡」文案。**必须点名目标的 artifact 与精确版本**（PRD 187/188）：
 * 只说"确认这一版"在有多条历史版本时无法核对，用户点下去其实不知道自己确认了什么。
 */
function confirmCard(artifact, revisionNo) {
  const a = artifact || {}
  return {
    title: '确认生效版本',
    // 与 PRD 第 187 行的口径一字对应：ID + 精确版本
    target: '成果 #' + a.artifactId + ' · v' + revisionNo,
    typeLabel: a.typeLabel || '',
    body: a.typeLabel
      ? '将该成果（' + a.typeLabel + '）的生效版本绑定到 v' + revisionNo + '。'
        + '此前生效的版本会转为历史版本，历史版本本身不会被修改。'
      : '将该成果的生效版本绑定到 v' + revisionNo + '。'
  }
}

/** 成果类型注册表（静态契约，不是租户数据）。编辑表单的字段契约来源。 */
function fetchArtifactTypes() {
  return request({ url: BASE + '/artifact-types', method: 'GET' })
}

/** 成果详情（含生效版本内容与缺项）。可见性由服务端判定：非参与方 404。 */
function fetchArtifact(artifactId) {
  return request({ url: BASE + '/artifacts/' + artifactId, method: 'GET' })
}

/** 版本历史（append-only 审计视图，与详情同可见性）。 */
function fetchRevisions(artifactId) {
  return request({ url: BASE + '/artifacts/' + artifactId + '/revisions', method: 'GET' })
}

/**
 * **编辑成果**：追加新版本。服务端语义是"不改变生效版本"——
 * 所以调用方必须把这件事如实告诉用户（见本段开头的第 1 条）。
 */
function appendRevision(artifactId, body, idempotencyKey) {
  return request({
    url: BASE + '/artifacts/' + artifactId + '/revisions',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** **确认成果**：把生效版本绑定到精确的 `revisionNo`（不是"最新"）。 */
function confirmArtifact(artifactId, revisionNo, idempotencyKey) {
  return request({
    url: BASE + '/artifacts/' + artifactId + '/confirm',
    method: 'POST',
    data: { revision_no: revisionNo },
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// UI-08 案件详情（异常 / 变更）—— ENT-030 切片四之四 · DR-0014
// ─────────────────────────────────────────────────────────────────────────────
//
// 本段是**只读投影**：拉 `GET /exceptions/{exception_id}`，把 PRD §4.1「UI-08」
// （第 155 行）要求的六要素摊平成模板直接可用的形状 —— 原因 / 受影响记录 /
// 拟解决方案 / 决定与审批 / 执行证据 / 结案。处置命令（links / decision / close /
// reopen）不在本片，`capabilities` 也**不**用来推断任何业务结论（理由见
// `decorateCase` 的注释）。
//
// ⚠️ 路径参数名与接口路径不一致是**有意的**：路由与深链用 `case_id`，接口路径沿用
//    既有的 `{exception_id}`（DR-0014 §7）。同一个值、两个名字 —— 不要「顺手统一」，
//    那会牵动一条已发布的接口路径。
//
// ⚠️ 取值域一律照抄后端 `exceptions.py` 的常量，**不在这里另立一套**：
//    `KINDS` / `IMPACT_KINDS` / `STATUSES` / `SEVERITIES` / `SOURCES` /
//    `TARGET_KINDS` / `DISPOSITIONS` / `EVENT_*` 与下表**逐字相等**，
//    `verify_entrust_ui.js` 做跨语言核对。少一个键，那一行就只剩原始英文值，
//    而界面看起来完全正常 —— 这正是最该被拦住的静默失效。

/** 案件类型（后端 `exceptions.KINDS`） */
const CASE_KIND_LABELS = { exception: '异常', change_request: '变更请求' }

/**
 * 影响类型（后端 `exceptions.IMPACT_KINDS`）—— 「轻重」看它，**不看 severity**。
 *
 * DR-0013 C3：严重度永不参与阻断判定，也同样不该在界面上决定排序或加重。
 * 影响类型才是**流程约束**：`execution-blocking` 会让 `complete_task` 直接 409。
 */
const CASE_IMPACT_LABELS = {
  informational: '仅供参考',
  'review-required': '需要复核',
  'execution-blocking': '阻断执行'
}

/**
 * 影响类型的徽标类。
 *
 * 阻断用红：它表达的是「这条路现在走不通」（流程约束），不是「这件事比较严重」——
 * 用同一个红去表达严重度，会让「严重但只是仅供参考」看起来和阻断一样，
 * 那正是 C3 要避免的联想。
 */
const CASE_IMPACT_CLASS = {
  informational: 'chip chip-muted',
  'review-required': 'chip chip-warn',
  'execution-blocking': 'chip chip-danger'
}

/**
 * 严重度（后端 `exceptions.SEVERITIES`）—— **只在详情页出现**（清单刻意不带它，
 * DR-0014 §3.2），且**不着色**：做成警示色等于把「按严重度决定流程」的联想请回来。
 */
const CASE_SEVERITY_LABELS = { low: '低', medium: '中', high: '高', critical: '严重' }

/** 案件状态（后端 `exceptions.STATUSES`）—— 两套状态机共用同一取值域 */
const CASE_STATUS_LABELS = {
  open: '待处理',
  in_review: '复核中',
  approved: '已批准',
  rejected: '已驳回',
  applied: '已应用',
  closed: '已关闭'
}

const CASE_STATUS_CLASS = {
  open: 'chip chip-warn',
  in_review: 'chip chip',
  approved: 'chip chip-success',
  rejected: 'chip chip-danger',
  applied: 'chip chip-purple',
  closed: 'chip chip-muted'
}

/** 案件来源（后端 `exceptions.SOURCES`）—— 谁提出的，影响"下一步找谁" */
const CASE_SOURCE_LABELS = {
  manual: '人工登记',
  chat: '会话中登记',
  customer: '客户提出',
  agent_proposal: 'Agent 建议'
}

/** 受影响项目标类型（后端 `exceptions.TARGET_KINDS`）—— 只有这两类，不放任任意目标 */
//: 受影响项类型 → 中文名。
//: `assignment` ＝ **本委托单本身**（2026-09-18 新增）：货量变更的落点是
//: `ent_assignment.quantity`，它不属于任何一份成果，所以必须有自己的名字 ——
//: 叫它"成果"会让人以为改的是某一版报价（而货量是委托的属性）。
const CASE_TARGET_LABELS = { task: '任务', artifact: '成果', assignment: '本委托货量' }

/**
 * 变更类别（后端 `revalidation.CHANGE_CATEGORIES`，DR-0016 的五行）。
 *
 * 这五个值决定**复核范围**：类别选错，复核就会去查不相干的对象，而任务照样生成、
 * 看起来完全正常。所以界面上必须把类别**显示出来**（不能只存在库里）——
 * 这是「这批复核任务凭什么生成的」唯一的可读答案。
 */
const CHANGE_CATEGORY_LABELS = {
  cargo_quantity_category: '货物数量与品类',
  origin_destination_mode: '起运地、目的地与运输方式',
  loading_delivery_window: '装卸与交付时间窗',
  selected_supplier_quote: '选定供应商与报价',
  approved_extra_charge: '已批准附加费用'
}

/**
 * 待复核项状态（后端 `ent_revalidation.status`）。
 *
 * `cancelled` **不是** `resolved`：前者是"复核要求被撤销"，后者是"已按当前版本核对过"。
 * 两者在界面上必须说不同的话 —— 合成一句「已处理」会让看的人以为核对过了。
 */
const REVALIDATION_STATUS_LABELS = { open: '待复核', resolved: '已复核', cancelled: '已撤销' }
const REVALIDATION_STATUS_CLASS = {
  open: 'chip chip-warn',
  resolved: 'chip chip-ok',
  cancelled: 'chip chip-muted'
}

/** 处置方式（后端 `exceptions.DISPOSITIONS`） */
const CASE_DISPOSITION_LABELS = {
  resolved: '已解决',
  accepted_residual: '接受遗留',
  cancelled: '撤销',
  duplicate: '重复登记',
  superseded: '被取代'
}

/** 事件类型（后端 `exceptions.EVENT_*`）—— 事件链的每一个点都要有个说法 */
const CASE_EVENT_LABELS = {
  created: '登记案件',
  status_changed: '状态变更',
  decided: '记录决定',
  link_added: '登记受影响项',
  link_removed: '移除受影响项',
  closed: '关闭案件',
  reopened: '重开案件',
  // A2 五之一（ENT-033）：「应用变更」成功与被拒都要在事件链上说清楚 ——
  // 少一个键，那一行的事件类型就只剩原始英文值，审计链会出现读不懂的一段。
  applied: '应用变更',
  applied_rejected: '应用被拒',
  // A2 五之二（ENT-033）：变更传播计划。它回答的是"下游收到了什么" ——
  // 与 `applied`（改了什么）是两件事，所以是两个事件、两个标签。
  revalidation_planned: '生成复核计划'
}

// ── UI-04 组织级队列的筛选取值域（DR-0014 §3.1）──────────────────────────────

/**
 * 组织级清单的**开闭范围**（后端 `exceptions.ORG_SCOPES`）。
 *
 * 为什么不是「状态筛选」：案件状态有六个（open / in_review / approved /
 * rejected / applied / closed），而组合工作台只关心一个问题 —— 「还有哪些没结」。
 * 六个状态摊成筛选条，「未关闭」这一档就得靠用户多选才能表达，而**漏选就是漏看**。
 * 后端因此也只认 `scope=unclosed|all`，并且明确拒绝 `status`（§3.1，混用一律 400）。
 */
const CASE_ORG_SCOPE_LABELS = { unclosed: '未关闭', all: '全部' }

/** 范围顺序（断言用，勿随意增删）—— 第一项即后端默认值 `ORG_SCOPE_UNCLOSED` */
const CASE_ORG_SCOPE_ORDER = ['unclosed', 'all']

/**
 * 案件类型筛选的**呈现顺序**（后端 `exceptions.KINDS` 只有两类）。
 *
 * 这个顺序只影响筛选条怎么排，不是取值域本身 —— 取值域由 `CASE_KIND_LABELS`
 * 的键集合与后端逐字核对（`verify_entrust_ui.js`）；后端那边是 frozenset，本无顺序。
 */
const CASE_KIND_ORDER = ['exception', 'change_request']

/**
 * 严重度 / 影响类型的呈现顺序（后端 `exceptions.SEVERITIES` / `IMPACT_KINDS`）。
 *
 * 与 `CASE_KIND_ORDER` 同理：顺序只影响选择条怎么排，取值域由标签表的键集合
 * 与后端逐字核对。`CASE_IMPACT_ORDER` 把 `execution-blocking` 放在**最后**不是
 * 随手排的 —— C2 规定它必须至少挂一条受影响项，排在末尾让「选了它就得补受影响项」
 * 在视觉上离受影响项区块最近（选择条下面紧跟着受影响项，而不是隔了三行）。
 */
const CASE_SEVERITY_ORDER = ['low', 'medium', 'high', 'critical']
const CASE_IMPACT_ORDER = ['informational', 'review-required', 'execution-blocking']

/**
 * 状态机 —— 后端 `exceptions._STATUS_TRANSITIONS` 的**逐字镜像**。
 *
 * 为什么界面上要有一份：`decide` 必须提交 `to_status`，而详情接口只回
 * `capabilities.can_decide` 这个布尔 —— 它说的是"现在能不能决定"，**没说什么能转**。
 * 只给布尔的话，界面要么把六个状态全列出来让用户去撞 409，
 * 要么自己另编一套「open 之后大概就是 in_review 吧」。
 *
 * ⚠️ 镜像 **不等于** 第二份真相：`scripts/verify_entrust_ui.js` 会把本表与后端
 *    `_STATUS_TRANSITIONS` 的解析结果**逐格**比对，差一格即失败；
 *    另有断言要求 `closed` 的出边只含 `open`（`reopen` 专用，`decide` 不得替代）。
 *    写端仍独立复核（`assert_transition`），本表只用于**呈现可选值**。
 */
const CASE_TRANSITIONS = {
  exception: {
    open: ['in_review', 'approved', 'rejected', 'closed'],
    in_review: ['approved', 'rejected', 'open'],
    approved: ['applied', 'rejected', 'closed'],
    rejected: ['in_review', 'open', 'closed'],
    applied: ['closed'],
    closed: ['open']
  },
  change_request: {
    open: ['in_review', 'rejected', 'closed'],
    in_review: ['approved', 'rejected', 'open'],
    approved: ['applied', 'rejected', 'closed'],
    rejected: ['closed'],
    applied: ['closed'],
    closed: ['open']
  }
}

/**
 * 关闭时的可选处置，按 `(kind, 关闭前状态)` 限定 —— 后端 `_CLOSURE_DISPOSITIONS` 镜像。
 *
 * 键拼成 `kind + '/' + from_status`：后端那边是**元组**键，JS 没有元组，
 * 拼字符串是唯一不引入额外结构（不用嵌套两层对象）的办法。这个不对称写在注释里，
 * 免得下一个人以为字符串是随手拼的。
 *
 * ⚠️ 两处**不是**「所有处置都能用」，且都是有意的：
 *   · `exception` 的 `rejected` 关闭**不含** `resolved` / `accepted_residual`
 *     —— §3.5「不允许通过驳回处置方案来解除真实异常」；
 *   · `change_request` 的 `approved` 关闭不含 `duplicate`
 *     —— 走到这一步已经确认「这是个真请求」，此时再说重复是自相矛盾。
 * 另有断言要求本表的键集合与后端**逐键**（含"某键存在但集合为空"这一情形）一致。
 */
const CASE_CLOSURE_DISPOSITIONS = {
  'exception/open': ['cancelled', 'duplicate', 'superseded'],
  'exception/approved': ['cancelled', 'duplicate', 'superseded'],
  'exception/rejected': ['cancelled', 'duplicate', 'superseded'],
  'exception/applied': ['resolved', 'accepted_residual'],
  'change_request/open': ['cancelled', 'duplicate', 'superseded'],
  'change_request/approved': ['cancelled', 'superseded'],
  'change_request/rejected': ['cancelled', 'duplicate', 'superseded'],
  'change_request/applied': ['resolved', 'accepted_residual', 'superseded']
}

/**
 * 「无需实际应用即可终结」的处置（后端 `_DISPOSITIONS_WITHOUT_APPLICATION`）。
 *
 * 单独列出来，是因为界面上要不要提示「关掉就再也不能改了」取决于它：
 * 用 `cancelled` / `duplicate` / `superseded` 关，说的其实是「这单本身不该存在」；
 * 用 `resolved` / `accepted_residual` 关，说的是「问题已解决」。
 * 两者对用户的代价完全不同，不能共用一句提示。
 */
const CASE_DISPOSITIONS_WITHOUT_APPLICATION = ['cancelled', 'duplicate', 'superseded']

/**
 * UI-08 的**六要素**（PRD 第 155 行）。这是本页内容的完整性依据：
 * `decorateCase` 按本表顺序产出 `blocks`，`verify_entrust_ui.js` 断言"产出与声明相等"
 * —— 少一个要素就是案件详情缺一块，而页面看起来只是"这一块没数据"。
 *
 * `mode` 是渲染形态：`text` 长文本 / `rows` 键值行 / `list` 条目列表。
 * `emptyText` 是**该要素为空时**的说法 —— 六句各不相同，且都不是"暂无数据"：
 * 用户要能分清「还没到那一步」与「这一块页面没做」。
 */
const CASE_ELEMENTS = [
  { key: 'cause', no: '①', title: '原因', mode: 'text', emptyText: '登记时没有填写原因' },
  {
    key: 'affected',
    no: '②',
    title: '受影响记录',
    mode: 'list',
    emptyText: '尚未登记受影响的任务或成果'
  },
  {
    key: 'proposed_action',
    no: '③',
    title: '拟解决方案',
    mode: 'text',
    emptyText: '还没有提出处理方案'
  },
  { key: 'decision', no: '④', title: '决定与审批', mode: 'rows', emptyText: '尚未作出决定' },
  { key: 'evidence', no: '⑤', title: '执行证据', mode: 'list', emptyText: '还没有执行证据引用' },
  { key: 'closure', no: '⑥', title: '结案', mode: 'rows', emptyText: '案件尚未关闭' }
]

/**
 * 用户 id → 可读文本。
 *
 * A1 的内部投影里只有 `actor_user_id`，**没有显示名**。这里如实显示编号，
 * 不编一个名字、也不留空白 —— 空白会让「谁做的」看起来像"没人做过"。
 * 显示名属独立的对客/内部投影决策，不在这条支线里顺手补。
 */
function _caseUser(id) {
  return id === null || id === undefined ? '' : '用户 #' + id
}

/** 时间原样透出；缺值给空串（模板据此不渲染那一行，而不是渲染一个假时间） */
function _caseTime(value) {
  return value ? String(value) : ''
}

/** 状态转移的一句话（`待处理 → 复核中`）；两端缺一就不编 */
function _caseMove(ev) {
  const from = CASE_STATUS_LABELS[ev.from_status]
  const to = CASE_STATUS_LABELS[ev.to_status]
  if (from && to) return from + ' → ' + to
  if (to) return '→ ' + to
  return ''
}

/**
 * 案件详情投影（UI-08 只读片）。
 *
 * 为什么本函数**不**从 `capabilities` 推出任何结论：
 *   ① `can_apply_change` 在 A1 **恒为 false**（应用变更属 A2）。据它说「你没有权限」
 *      就是把「功能还没做」说成「你不被允许」—— 同一句话在两种情况下含义不同，
 *      而用户无从分辨；
 *   ② 其余五个是 `can_write AND 当前状态允许` 的**合取**。全为 false 时既可能是
 *      「没授权」也可能是「当前确实没有可执行动作」，**从六个布尔值里分不出来**。
 * 所以本片只给一句对两种读法都成立的话（`actionHint`）；按钮可见性由下一片按
 * 各自的 `can_*` 决定 —— 那是不会判错的用法（有则显示、无则隐藏），
 * 且**权限判定始终在写端**，界面隐藏按钮从来不是权限控制。
 */
function decorateCase(payload, typeLabels) {
  const res = payload || {}
  const data = res.case || {}
  const caps = res.capabilities || {}
  const events = res.events || []
  const decision = data.decision || {}
  const closure = data.closure || {}
  const resolution = data.resolution || {}
  const affected = data.affected || []

  // ⑤ 执行证据**只**取事件链的 `evidence_ref`（§3.1.3 要求 `closed` 事件携带它）。
  //    受影响项的 `applied_revision_id` 说的是「应用过哪个版本」，那是另一件事 ——
  //    混进来会把"有证据"变得含糊，而证据是否充分恰恰是结案时最容易起争议的一点。
  const evidence = events
    .filter(function (ev) {
      return !!ev.evidence_ref
    })
    .map(function (ev) {
      return {
        key: 'ev-' + ev.seq,
        text: ev.evidence_ref,
        sub: (CASE_EVENT_LABELS[ev.event_kind] || ev.event_kind || '事件') + ' · 第 ' + ev.seq + ' 条',
        meta: _caseTime(ev.created_at)
      }
    })

  const fill = {
    cause: function () {
      return { text: data.cause || '' }
    },
    affected: function () {
      return {
        items: affected.map(function (item) {
          const applied = item.applied_revision_id
          const has = applied !== null && applied !== undefined
          return {
            key: 'lk-' + item.link_id,
            // `linkId` 是**移除受影响项**要的参数（`DELETE .../links/{link_id}`）。
            // 不放进 `text` 里让页面去截字符串 —— 那会把"显示什么"与"提交什么"绑死。
            linkId: item.link_id,
            // ⚠️ `target_kind` / `target_id` 必须**原样带出来**：`change_key` 是
            // `"{target_kind}#{target_id}"`，批准时的 `approved_changes` 要用它拼键。
            // 只给 `text`（`任务 #3`）不给原值，页面就只能去解析那行中文 ——
            // 而"解析显示文案"是本仓明确禁止的（显示改一次，提交就静默失效）。
            // 症状是"货量变更填了却提交不出去"，页面上看起来什么都正常。
            target_kind: item.target_kind || '',
            target_id:
              item.target_id === null || item.target_id === undefined
                ? ''
                : String(item.target_id),
            text:
              (CASE_TARGET_LABELS[item.target_kind] || item.target_kind || '目标') +
              ' #' +
              item.target_id,
            sub: has ? '已应用版本 r' + applied : '未应用任何版本',
            // 「已应用」是**有据可查**的事实（close 对 resolved 要求每条都写过它），
            // 用颜色标出来；反之不标 —— 没应用不代表出错，只是还没走到那一步。
            // 类名在投影层算好、模板不拼：模板里拼类名会让"类是否存在"变成运行期才知道
            // （与本文件既有的 `statusClass` / 槽位 `cls` 同一口径）。
            cls: has ? 'element-item element-item-done' : 'element-item'
          }
        })
      }
    },
    proposed_action: function () {
      return { text: data.proposed_action || '' }
    },
    decision: function () {
      const rows = []
      if (decision.note) rows.push({ key: 'note', label: '决定说明', value: decision.note })
      if (decision.by !== null && decision.by !== undefined) {
        rows.push({ key: 'by', label: '决定人', value: _caseUser(decision.by) })
      }
      if (decision.at) rows.push({ key: 'at', label: '决定时间', value: decision.at })
      if (decision.basis_revision_id !== null && decision.basis_revision_id !== undefined) {
        // 显示的是**成果版本的 id**（`ent_artifact_revision.id`），**不是** `revision_no`。
        // 两者是不同的数：写成 `rN` 会与工作台那个真正的 `rN`（revision_no）混为一谈，
        // 而案件页「批准」要填的正是这个 id —— 口径不一致，用户就会去填另一个号。
        rows.push({
          key: 'basis',
          label: '依据版本',
          value: '版本 id ' + decision.basis_revision_id
        })
      }
      return { rows: rows }
    },
    evidence: function () {
      return { items: evidence }
    },
    closure: function () {
      const rows = []
      if (closure.disposition) {
        rows.push({
          key: 'disp',
          label: '处置方式',
          value: CASE_DISPOSITION_LABELS[closure.disposition] || closure.disposition
        })
      }
      // 结案说明放在**处置方式之后**：先说怎么处置的，再说为什么这么处置。
      if (resolution.note) rows.push({ key: 'res', label: '结案说明', value: resolution.note })
      if (closure.by !== null && closure.by !== undefined) {
        rows.push({ key: 'by', label: '关闭人', value: _caseUser(closure.by) })
      }
      if (closure.at) rows.push({ key: 'at', label: '关闭时间', value: closure.at })
      return { rows: rows }
    }
  }

  const blocks = CASE_ELEMENTS.map(function (el) {
    const extra = fill[el.key] ? fill[el.key]() : {}
    return Object.assign(
      {
        key: el.key,
        no: el.no,
        title: el.title,
        mode: el.mode,
        emptyText: el.emptyText,
        text: '',
        rows: [],
        items: []
      },
      extra
    )
  })

  // 六项写能力：A1 的五个处置 + A2 五之四的「应用变更」。
  // ⚠️ `can_apply_change` 自 ENT-041 起**不再恒 false**（`APPLY_OPEN` 已翻 True，
  //    前置是五之二传播闭环 + AC-12 阻止确认都已接通）。此前它在这里被排除，
  //    理由是"它恒 false、据它下任何界面结论都是错的"—— 那个理由现在**已失效**，
  //    留着就会让「应用变更」区永远不出现，而看起来像前端没接线。
  const writeCaps = [
    'can_add_link',
    'can_remove_link',
    'can_decide',
    'can_close',
    'can_reopen',
    'can_apply_change'
  ]
  const hasAction = writeCaps.some(function (k) {
    return !!caps[k]
  })

  const owner = data.owner_user_id
  // `case_id` / `assignment_id` 是接口的必填字段，正常不会缺。但缺了就把 `#undefined`
  // 摆到界面上，是**编造了一个值**而不是报告缺失 —— 用同一口径转成「—」。
  const caseIdText = data.case_id === null || data.case_id === undefined ? '—' : '#' + data.case_id
  const assignmentIdText =
    data.assignment_id === null || data.assignment_id === undefined ? '—' : '#' + data.assignment_id
  return {
    caseId: data.case_id,
    assignmentId: data.assignment_id === null || data.assignment_id === undefined
      ? ''
      : String(data.assignment_id),
    orgId: data.org_id === null || data.org_id === undefined ? '' : String(data.org_id),
    kind: data.kind || '',
    kindLabel: CASE_KIND_LABELS[data.kind] || data.kind || '',
    // 变更类别（A2 五之二）：决定**复核范围**。页面要它来回答两个问题 ——
    // ① 这批复核凭什么生成的（展示）；② 本次决定要不要连带给「变更内容」
    //    （只有货量类变更在界面上有可填的变更内容，见 case.js 的 `showQuantityChange`）。
    // 取值域镜像缺项时**退回原值**，不编一个看起来对的中文名。
    changeCategory: data.change_category || '',
    changeCategoryLabel:
      CHANGE_CATEGORY_LABELS[data.change_category] || data.change_category || '未登记',
    title: data.title || '未命名案件',
    status: data.status || '',
    statusLabel: CASE_STATUS_LABELS[data.status] || data.status || '',
    statusClass: CASE_STATUS_CLASS[data.status] || 'chip chip-muted',
    // 阻断是**流程后果**，与影响类型互为印证：两个都显示，用户不必自己去推
    blocking: !!data.blocking,
    impactLabel: CASE_IMPACT_LABELS[data.impact_kind] || data.impact_kind || '',
    impactClass: CASE_IMPACT_CLASS[data.impact_kind] || 'chip chip-muted',
    severityLabel: CASE_SEVERITY_LABELS[data.severity] || data.severity || '',
    sourceLabel: CASE_SOURCE_LABELS[data.source] || data.source || '',
    revisionNo: data.revision_no,
    fields: [
      { key: 'no', label: '案件号', value: caseIdText },
      { key: 'as', label: '所属委托', value: assignmentIdText },
      { key: 'imp', label: '影响类型', value: CASE_IMPACT_LABELS[data.impact_kind] || data.impact_kind || '' },
      { key: 'sev', label: '严重度', value: CASE_SEVERITY_LABELS[data.severity] || data.severity || '' },
      { key: 'src', label: '来源', value: CASE_SOURCE_LABELS[data.source] || data.source || '' },
      { key: 'own', label: '责任人', value: owner === null || owner === undefined ? '未指定' : _caseUser(owner) },
      { key: 'due', label: '截止时间', value: data.due_at || '未设置' },
      { key: 'upd', label: '最后更新', value: _caseTime(data.updated_at) },
      { key: 'rev', label: '数据版本', value: 'r' + (data.revision_no || 1) }
    ],
    blocks: blocks,
    // ── A2 五之四：复核传播与批准快照（界面输入）──────────────────────────
    // 两项都是**派生展示**，不参与任何权限判断：写端各自独立复核权限与版本。
    revalidation: (res.revalidation || []).map(function (row) {
      const hasTarget = !!row.target_kind && row.target_id !== null && row.target_id !== undefined
      return {
        key: row.review_key,
        area: row.area || '',
        taskTypeLabel: TASK_TYPE_LABELS[row.task_type] || row.task_type || '',
        // 目标与版本分两行显示：`target_revision_id` 是**成果版本的 id**（不是 `revision_no`），
        // 缺它时说明这条复核没有具体对象（DR-0016 §4.3 的"无对象区域任务"）。
        targetText: hasTarget
          ? (CASE_TARGET_LABELS[row.target_kind] || row.target_kind) + ' #' + row.target_id
          : '无具体对象',
        revisionText:
          row.target_revision_id === null || row.target_revision_id === undefined
            ? '未绑定版本'
            : '版本 id ' + row.target_revision_id,
        reviewTaskId: row.review_task_id,
        taskTitle: row.task_title || '（复核任务标题缺失）',
        taskStatusLabel: TASK_STATUS_LABELS[row.task_status] || row.task_status || '',
        status: row.status,
        statusLabel: REVALIDATION_STATUS_LABELS[row.status] || row.status || '',
        statusClass: REVALIDATION_STATUS_CLASS[row.status] || 'chip chip-muted',
        // note 只在**存在时**才显示：`cancelled` 的原因写在这里（"复核要求被撤销"），
        // 空字符串会在模板里渲染成一行空白，看起来像排版坏了。
        note: row.note || ''
      }
    }),
    // DR-0016 §4.1：映射点名、委托里确实存在、却没被登记为受影响项的**成果类型**。
    // 这是**提问**不是结论 —— 界面上要说"请确认范围"，不能说"已自动补上"。
    //
    // 类型中文名从调用方传入的 `typeLabels`（来自 `GET /artifact-types` 的注册表）取。
    // 前端**不另存一份类型标签表**：注册表的 label 是要给客户看的措辞，抄一份就会漂移，
    // 而漂移的表现是"复核清单里的类型名和成果页里的不一样"。取不到时**退回原值**，
    // 不编造一个看起来对的中文名。
    unconfirmedTypes: (res.unconfirmed_types || []).map(function (code) {
      const labels = typeLabels || {}
      return { code: code, label: labels[code] || code }
    }),
    approval: res.approval
      ? {
          snapshotVersion: res.approval.snapshot_version,
          categoryLabel:
            CHANGE_CATEGORY_LABELS[res.approval.change_category] ||
            res.approval.change_category ||
            '',
          targets: (res.approval.targets || []).map(function (t) {
            const fields = t.change_fields || []
            return {
              key: t.target_kind + '#' + t.target_id,
              targetText:
                (CASE_TARGET_LABELS[t.target_kind] || t.target_kind) + ' #' + t.target_id,
              // 字段名翻成中文（复用成果页的字段标签表）—— 直接摆英文键名，
              // 读的人得回去翻成果页才知道那几个键是什么意思。
              fieldsText: fields.length
                ? fields
                    .map(function (name) {
                      return artifactFieldLabel(name)
                    })
                    .join('、')
                : '无字段变更（任务类目标由任务状态机承担）',
              basisText:
                t.basis_revision_id === null || t.basis_revision_id === undefined
                  ? '无基础版本'
                  : '版本 id ' + t.basis_revision_id,
              // 委托货量变更**必须**把 `before → after` 摆出来：其余目标的载荷是不定形的
              // （只列字段名合理），而这一类的值就是两个标量 ——
              // 不显示它，「确认应用」就退化成盲操作。
              // ⚠️ 文案取**服务端**给的 `quantity_text`，前端不拼 `数值 + 单位`：
              // 各拼一份必然漂移，而漂移的表现是"历史里写 800 吨、这里写 800.000吨"。
              quantityChangeText: t.quantity_change
                ? '将改为 ' +
                  ((t.quantity_change.after || {}).quantity_text || '') +
                  '（当前 ' +
                  ((t.quantity_change.before || {}).quantity_text || '未知') +
                  '）'
                : '',
              quantityBasisText:
                t.quantity_change && t.quantity_change.basis
                  ? '变更依据：' + t.quantity_change.basis
                  : ''
            }
          })
        }
      : null,
    events: events.map(function (ev) {
      // 逐项拼接、缺项不留悬空分隔符：`… + ' · ' + …` 在缺值时会渲染出开头的
      // 「 · 操作人 …」，看起来像排版坏了 —— 而这页最容易缺的恰恰是时间与操作人。
      const parts = []
      const time = _caseTime(ev.created_at)
      if (time) parts.push(time)
      const actor = _caseUser(ev.actor_user_id)
      if (actor) parts.push('操作人 ' + actor)
      const move = _caseMove(ev)
      if (move) parts.push(move)
      return {
        seq: ev.seq,
        title: CASE_EVENT_LABELS[ev.event_kind] || ev.event_kind || '事件',
        meta: parts.join(' · '),
        note: ev.note || '',
        evidence: ev.evidence_ref || ''
      }
    }),
    capabilities: {
      can_add_link: !!caps.can_add_link,
      can_remove_link: !!caps.can_remove_link,
      can_decide: !!caps.can_decide,
      can_close: !!caps.can_close,
      can_reopen: !!caps.can_reopen,
      can_apply_change: !!caps.can_apply_change
    },
    actionHint: hasAction
      ? '你有处置这宗案件的权限'
      : '按当前状态与你的授权，这里没有可执行的处置动作'
  }
}

/**
 * 拉取案件详情。
 *
 * 路径参数用 `case_id` 的值、走 `{exception_id}` 的路径（DR-0014 §7）。
 * 可见性由服务端判定：非参与方 **404**，且与「开关关闭」同码 —— 服务端刻意不区分
 * 「不存在」与「无权知晓」，界面因此也不能替它下结论（见 `case.js` 的 404 说明）。
 */
function fetchCase(caseId) {
  return request({ url: BASE + '/exceptions/' + caseId, method: 'GET' })
}

// ─────────────────────────────────────────────────────────────────────────────
// UI-04 组织级异常 / 变更队列（ENT-030 切片四之六 · DR-0014 §3.1–3.2）
// ─────────────────────────────────────────────────────────────────────────────
//
// 这一节补的是**跨委托**的组合队列：`GET /exceptions?view=org&org_id=…`。
// 与单委托视图（`GET /exceptions?assignment_id=…`，给完整投影）不同，组织视图
// 只给清单所需的最小行（`ExceptionCaseListItem`）—— 组合工作台没有"同时展开
// N 宗案件详情"的用途，而每宗案件详情都带整条事件链。
//
// ⚠️ **行形状刻意不含 `severity`**（DR-0014 §3.2）。界面上「轻重」由两个字段表达：
//    `blocking`（流程约束，会让 `complete_task` 直接 409）与 `impact_kind`（影响类型）。
//    清单里给出 severity，界面迟早会拿它排序或加重 —— 那正是 C3 要堵住的联想。
//    所以本节的投影里**没有任何 severity 字段**，不是漏了。

/** 数值 ID → 可读文本；缺值给 `—`（摆 `#undefined` 是编造一个值，不是报告缺失） */
function _caseIdText(value) {
  return value === null || value === undefined ? '—' : '#' + value
}

/**
 * 组织级清单的一行 → 模板直接可用的形状。
 *
 * `assignmentText` 是必须的：UI-04 是**跨委托**的组合队列，不写「是哪张单的」，
 * 用户就只能点进去才知道 —— 而这张清单的全部价值就是"不进详情也能分诊"。
 */
function decorateCaseRow(row) {
  const data = row || {}
  const assignmentId = data.assignment_id
  const count = data.affected_count
  return {
    // `caseId` 是**原值**（进路由参数），`caseNo` 才带 `#`（给人看）。
    // 两者不能合成一个字段：带 `#` 的值直接拼进 `/exceptions/{case_id}` 就是一条错路径，
    // 而这种错误在界面上只表现为"点进去报错"，看不出是拼串拼坏的。
    caseId: data.case_id === null || data.case_id === undefined ? '' : String(data.case_id),
    caseNo: _caseIdText(data.case_id),
    title: data.title || '未命名案件',
    kindLabel: CASE_KIND_LABELS[data.kind] || data.kind || '',
    statusLabel: CASE_STATUS_LABELS[data.status] || data.status || '',
    statusClass: CASE_STATUS_CLASS[data.status] || 'chip chip-muted',
    impactLabel: CASE_IMPACT_LABELS[data.impact_kind] || data.impact_kind || '',
    impactClass: CASE_IMPACT_CLASS[data.impact_kind] || 'chip chip-muted',
    // 阻断是**流程后果**，与影响类型互为印证：两个都显示，用户不必自己推。
    // 它在清单里比在详情里更要紧 —— 这一行就是"哪些单现在走不动"的答案。
    blocking: !!data.blocking,
    assignmentText:
      assignmentId === null || assignmentId === undefined ? '未标注委托' : '委托 #' + assignmentId,
    affectedText: count ? '受影响 ' + count + ' 项' : '未登记受影响项',
    dueText: data.due_at ? '截止 ' + data.due_at : '未设置截止时间',
    updatedAt: data.updated_at ? String(data.updated_at) : ''
  }
}

function decorateCaseList(rows) {
  return (rows || []).map(decorateCaseRow)
}

/**
 * 组织级清单的查询参数（**纯函数**，`scripts/verify_entrust_ui.js` 直接驱动）。
 *
 * 为什么不把参数拼在 `fetchCaseOrgList` 里：这一节最容易错的地方不是"发没发请求"，
 * 而是"带没带对范围" —— 缺 `org_id` 后端直接 422，带上别的组织的 `org_id` 会
 * 403/404，两种都不会在界面上显出异常（一个有错的队列也是队列）。做成纯函数
 * 才能在 CI 里把参数逐个钉住。
 *
 * `org_id` **恒在**，缺它就抛 —— 后端明确不提供「我所属全部组织」这种无范围查询
 * （DR-0014 §3.1），那是 HO 禁止的跨组织拼接。宁可在这里就拒绝，也不要发出一次
 * 注定失败的请求：那会把「界面还没定位到组织」渲染成「加载失败」。
 */
function caseOrgListQuery(options) {
  const opts = options || {}
  const orgId = opts.orgId === null || opts.orgId === undefined ? '' : String(opts.orgId)
  if (!orgId) {
    throw new Error('组织级案件清单必须给出 orgId（缺范围请求后端会 422）')
  }
  const data = {
    view: 'org',
    org_id: orgId,
    scope: opts.scope || CASE_ORG_SCOPE_ORDER[0],
    page: opts.page || 1,
    size: opts.size || 20
  }
  // `kind` 只在真的选了类型时带上：传空串等于"筛了个空"，看起来筛了其实没筛。
  if (opts.kind) data.kind = opts.kind
  return data
}

/**
 * 拉取组织级案件清单（UI-04 队列的数据源）。
 *
 * 可见性与范围都由服务端判定：非参与方 404（与「开关关闭」同码，服务端刻意不区分），
 * 越权组织 403/404。所以界面拿到的 404 只能报「功能未开放」，**不能**替服务端
 * 下「这个组织没有案件」的结论 —— 与案件详情页同一条口径。
 */
function fetchCaseOrgList(options) {
  return request({ url: BASE + '/exceptions', method: 'GET', data: caseOrgListQuery(options) })
}

// ─────────────────────────────────────────────────────────────────────────────
// 案件的人工处置（写路径 · ENT-030 切片四之六 / DR-0013 §7.1 A1 出口）
// ─────────────────────────────────────────────────────────────────────────────
//
// ── 六个写命令为什么集中在这里，而不是各页各写一份 ────────────────────
// `Idempotency-Key` 的生成时机、409（版本过期 / 重复登记）与 403（无权限）的分流
// 话术、以及「提交成功后重新取数」的口径 —— 六个命令**完全一致**。两个页面各写一遍
// 就是把这个口径抄两份，将来某一页把 409 提示改成「请重试」，用户就会照着点，
// 然后拿到第二个 409。所以写命令只此一份，页面只负责收集输入与展示结论。
//
// ── 幂等键的归属：由调用方传入，不在这里 new ─────────────────────────
//   · 一次用户动作 = 一个键（在表单里改两处再点提交，仍然是一次动作）；
//   · 提交失败后**重试同一件事**必须复用同一个键 —— 否则一次网络抖动会留下两条
//     一模一样的记录，而重复登记在 A1 是 409，用户只会以为是"系统坏了"；
//   · 用户改了内容再提交 = 新意图 = 新键。
// 「这是重试还是新意图」只有页面知道（它拿着表单状态），所以键在页面生成，这里透传。
//
// ── 为什么这些写命令要 `silent` ──────────────────────────────────────
// 请求层默认把服务端 `detail` 直接 toast 出来。对业务写操作这不够：
// 409 的正确下一步是「刷新后按当前状态重来」，而用户看到一句
// 「非法状态转移：exception 不能从 closed 转到 closed」只会再点一次。
// 所以这里关掉自动提示，改由 `caseWriteError` 把状态码翻译成**可执行的结论**，
// 页面负责呈现；网络层错误（没有 httpStatus）仍复用请求层的诊断文案。

/**
 * 已关闭状态（后端 `exceptions.STATUS_CLOSED`）。
 *
 * 单独提成一个常量，因为本文件有三处要判它（决定选项、关闭选项、写命令回读），
 * 而 `'closed'` 这个字面量散在三处时，改一处漏两处不会有任何东西报错。
 */
const CASE_STATUS_CLOSED = 'closed'

/**
 * `decide` **不得**用来关案件 —— 后端 `decide` 对 `to_status='closed'` 直接 **409**
 * （「关闭案件请走 close 命令（必须给出处置与证据），不能借 decide 关闭」）。
 *
 * 状态机里 `open → closed` 是合法转移，但它属于 **close** 命令那条路径。
 * 于是「照状态机列决定选项」会列出一个必然 409 的选项 —— 这正是镜像比真相更细的地方，
 * 单独列成常量并由 CI 钉住（断言这条排除项与后端 `decide` 的 409 分支一致）。
 */
const CASE_DECIDE_EXCLUDED = [CASE_STATUS_CLOSED]

/** 一个选择条的选项数组（`key`+`label`）。页面**不要**自己 `Object.keys` 标签表。 */
function caseOptionList(order, labels) {
  return (order || []).map(function (key) {
    return { key: key, label: labels[key] || key }
  })
}

/** 案件类型选择条（登记时用） */
function caseKindOptions() {
  return caseOptionList(CASE_KIND_ORDER, CASE_KIND_LABELS)
}

/** 严重度选择条 */
function caseSeverityOptions() {
  return caseOptionList(CASE_SEVERITY_ORDER, CASE_SEVERITY_LABELS)
}

/** 影响类型选择条 */
function caseImpactOptions() {
  return caseOptionList(CASE_IMPACT_ORDER, CASE_IMPACT_LABELS)
}

/**
 * 记录决定的**可选目标状态**（只取镜像状态机，不另做判断）。
 *
 * ⚠️ 这个函数**不判权限** —— `can_decide` 是服务端给的结论，页面凭它决定要不要
 *    渲染这个区块。这里只回答"能转到哪"。
 * ⚠️ 排除 `closed`：它虽然是一条合法转移，但只能由 `close` 走（见 `CASE_DECIDE_EXCLUDED`）。
 * ⚠️ **已关闭案件一律返回空**：状态机里 `closed → open` 是合法转移，但那是 `reopen`
 *    的命令地盘，`decide` 走它只会写成另一种审计事件、且被 `case_capabilities` 判为
 *    不可用（`can_decide` 对已关闭恒 false）。返回 `[open]` 会给出一个"看起来能用、
 *    实际必被拒"的选项 —— 这类空列表比错列表好，所以这里显式短路。
 */
function caseDecisionOptions(kind, status) {
  if (status === CASE_STATUS_CLOSED) return []
  const byKind = CASE_TRANSITIONS[kind] || {}
  const list = byKind[status] || []
  return list
    .filter(function (key) {
      return CASE_DECIDE_EXCLUDED.indexOf(key) === -1
    })
    .map(function (key) {
      return { key: key, label: CASE_STATUS_LABELS[key] || key }
    })
}

/**
 * 该不该渲染「记录决定」区块 = `can_decide` **且** 至少有一个可选目标状态。
 *
 * 为什么要 `&&`（这是本轮实测出来的缺口，不是防患于未然）：
 * 后端 `can_decide = can_write and open_case and bool(allowed_transitions(kind, status))`
 * —— 它只问"状态机从当前状态**有没有出边**"。而
 * `change_request/rejected` 与 `exception/applied` 的出边**只剩 `closed`**，
 * 那一条归 `close` 所有（`decide` 走它会 409）。于是这两个状态下后端给
 * `can_decide=true`、界面却**一个选项都列不出来** —— 光看 `can_decide` 就会渲染出
 * 一个空的选择条，用户只会以为界面坏了。
 *
 * ⚠️ 这是**呈现层**的补位，不是第二个权限判据：它只会让按钮**少**出现，
 *    绝不会让没权限的人多出一个按钮（`can_decide` 仍是必要条件）。
 *    真正的判据仍在写端（`_assert_can_write` + `assert_transition`）。
 */
function caseDecideAvailable(capabilities, kind, status) {
  const caps = capabilities || {}
  return !!caps.can_decide && caseDecisionOptions(kind, status).length > 0
}

/** 关闭案件的**可选处置方式**（按 `kind` + 当前状态查镜像表；空数组＝当前不可关闭） */
function caseClosureOptions(kind, status) {
  const list = CASE_CLOSURE_DISPOSITIONS[kind + '/' + status] || []
  return list.map(function (key) {
    return { key: key, label: CASE_DISPOSITION_LABELS[key] || key }
  })
}

/** 该处置是否属于「这单本身不该存在」（关掉即终结，不走实际应用） */
function isDispositionWithoutApplication(disposition) {
  return CASE_DISPOSITIONS_WITHOUT_APPLICATION.indexOf(String(disposition || '')) !== -1
}

/**
 * **变更类别**的可选项（A2 五之二 / DR-0016 五行）。
 *
 * 只有变更请求有类别 —— 异常案件带它会 400（`_require_known_change_category`），
 * 所以这里按 `kind` 收窄，返回值直接可以拿去渲染选择条。
 *
 * 为什么页面上必须有这个入口：应用变更**要求**类别已登记（没有类别就无从确定复核
 * 范围，而"先应用、后补范围"会让下游照着失效事实干活）。而登记时未必知道该归哪一类 ——
 * 服务端把"批准"当作补登记的**最后合理时机**（那时决定人正看着这份变更的内容）。
 * 界面此前没有这个入口 ⇒ 类别只能靠种子/接口写进去，**变更请求在界面上根本应用不了**。
 */
function caseCategoryOptions(kind) {
  if (String(kind) !== 'change_request') return []
  return Object.keys(CHANGE_CATEGORY_LABELS).map(function (key) {
    return { key: key, label: CHANGE_CATEGORY_LABELS[key] }
  })
}

/**
 * 登记案件表单 → 请求体（`ExceptionCaseCreate`）。**只做前置检查，不替代服务端规则。**
 *
 * 三条纪律：
 * 1. **前置检查不是替代**：C1/C2 在这里查一遍只是让用户在点提交**之前**就看到原因；
 *    写端仍独立复核（`assert_severity_impact_consistent` / `assert_blocking_requires_link`），
 *    界面这份松一点也不会放行任何东西，紧一点也只是少一次往返。
 * 2. **空值不发键**：可选字段为空就**不带这个键**，而不是带 `""`。带空串会被 Pydantic
 *    当成"你给了一个空的值"，报出来的错与用户刚才的操作对不上。
 * 3. **未知值保持未知**：`due_at` 原样透传页面给的字符串（日期选择器给什么就是什么），
 *    这里不补时分秒、不猜时区。
 *
 * @returns {{ok:boolean, errors:string[], body:object}}
 */
function caseCreateBody(form) {
  const f = form || {}
  const kind = String(f.kind || '')
  const title = String(f.title || '').trim()
  const severity = String(f.severity || '')
  const impact = String(f.impact_kind || '')
  const links = (f.links || []).map(function (it) {
    return { target_kind: String((it && it.target_kind) || ''), target_id: Number((it && it.target_id) || 0) }
  })

  const errors = []
  if (!CASE_KIND_LABELS[kind]) errors.push('请选择案件类型')
  if (!title) errors.push('请填一句话摘要')
  else if (title.length > 200) errors.push('一句话摘要超过 200 字')
  if (!CASE_SEVERITY_LABELS[severity]) errors.push('请选择严重度')
  if (!CASE_IMPACT_LABELS[impact]) errors.push('请选择影响类型')
  // C1 的界面侧投影：与后端同向，只是提前说出来
  if (severity === 'critical' && impact && impact !== 'execution-blocking') {
    errors.push('严重度选「严重」时，影响类型必须是「阻断执行」')
  }
  // C2 的界面侧投影：阻断必须至少有一条受影响项
  if (impact === 'execution-blocking' && links.length === 0) {
    errors.push('影响类型为「阻断执行」时，必须至少登记一条受影响项')
  }
  if (
    links.some(function (it) {
      return !CASE_TARGET_LABELS[it.target_kind] || !(it.target_id > 0)
    })
  ) {
    errors.push('受影响项的编号或类型不合法')
  }

  const body = { kind: kind, title: title, severity: severity, impact_kind: impact, source: 'manual' }
  if (f.cause) body.cause = String(f.cause)
  if (f.proposed_action) body.proposed_action = String(f.proposed_action)
  if (f.due_at) body.due_at = String(f.due_at)
  if (links.length) body.links = links
  return { ok: errors.length === 0, errors: errors, body: body }
}

/**
 * 案件写失败 → **可执行的结论**（页面据此提示）。
 *
 * 逐码分流，而不是把服务端 `detail` 转手抛给用户：
 *   · **409** 状态冲突（非法转移 / 版本过期 / 重复登记）→ 唯一正确的下一步是**重新取数**，
 *     所以话术必须包含"已刷新，请按当前状态重来"，而这句话只有页面能兑现；
 *   · **403** 无权限 / 作用域不符 → 刷新不会改变结论，别让用户白点；
 *   · **400** 规则违反（C1/C2、缺证据…）→ 服务端 `detail` 本身就是最准的说明，原样带上；
 *   · **404** 与读路径同一口径：开关关闭 / 不存在 / 非参与方**刻意同码**，
 *     所以只能说"功能未开放"，不能替服务端下"案件不存在"这个结论；
 *   · **0**（无 httpStatus）→ 网络层，复用请求层的诊断文案（域名/代理/后端未启动）。
 *
 * @returns {{kind:string, title:string, hint:string, conflict:boolean}}
 */
function caseWriteError(err) {
  const status = (err && err.httpStatus) || 0
  const detail = (err && err.detail) ? String(err.detail) : ''
  if (status === 409) {
    return {
      kind: 'conflict',
      title: '状态或版本已变化',
      conflict: true,
      hint: detail || '有人在你之前改动了这宗案件。已按当前状态重新取数，请确认后重来。'
    }
  }
  if (status === 403) {
    return {
      kind: 'denied',
      title: '当前身份不能做这个操作',
      conflict: false,
      hint: detail || '这宗案件所属组织与你的授权不匹配。换一个身份或让管理员补充授权后再试。'
    }
  }
  if (status === 400) {
    return {
      kind: 'invalid',
      title: '按当前信息不能这么记',
      conflict: false,
      hint: detail || '服务端校验未通过，请按提示修改后重试。'
    }
  }
  if (status === 404) {
    return {
      kind: 'notfound',
      title: '功能未开放或无权查看',
      conflict: false,
      hint: detail || '委托发货功能可能未开放，或这宗案件不在你的可见范围内。'
    }
  }
  return {
    kind: 'network',
    title: '提交失败',
    conflict: false,
    hint: '网络层异常：请确认后端已启动、且没有代理拦截请求。已保留你填的内容，可直接重试。'
  }
}

/**
 * 登记案件（`POST /assignments/{id}/exceptions`）。
 *
 * `links` 允许**随案件一起提交**，这不是便利，而是 C2 的必然：
 * 阻断类案件必须先有受影响项才合法，若只能"先建案件、再补 link"，
 * 那个中间态本身就违反 C2（后端 `raise_case` 也是同事务写入，见其注释）。
 */
function createCase(assignmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/exceptions',
    method: 'POST',
    data: body,
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 给已有案件补登记一条受影响项（`expected_revision` 是**案件**的乐观锁） */
function addCaseLink(caseId, body, idempotencyKey) {
  return request({
    url: BASE + '/exceptions/' + caseId + '/links',
    method: 'POST',
    data: body,
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 移除一条受影响项（登记错了要能更正，否则 `resolved` 永不可达） */
function removeCaseLink(caseId, linkId, expectedRevision, idempotencyKey) {
  return request({
    url:
      BASE +
      '/exceptions/' +
      caseId +
      '/links/' +
      linkId +
      '?expected_revision=' +
      encodeURIComponent(String(expectedRevision)),
    method: 'DELETE',
    data: {},
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 记录决定（`to_status` / `decision_note` / `basis_revision_id`；`approved` 必须给依据版本） */
function decideCase(caseId, body, idempotencyKey) {
  return request({
    url: BASE + '/exceptions/' + caseId + '/decision',
    method: 'POST',
    data: body,
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 关闭案件（必须给处置与证据 —— 没有一键关闭） */
function closeCase(caseId, body, idempotencyKey) {
  return request({
    url: BASE + '/exceptions/' + caseId + '/close',
    method: 'POST',
    data: body,
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 重开已关闭案件（原因必填，落审计事件的 `note`） */
function reopenCase(caseId, body, idempotencyKey) {
  return request({
    url: BASE + '/exceptions/' + caseId + '/reopen',
    method: 'POST',
    data: body,
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 应用已批准的变更（`POST /exceptions/{id}/apply`；A2 五之一，界面接入见五之四）。
 *
 * 请求体**只有 `expected_revision`** —— 这是刻意的：修改内容只来自批准快照
 * （`decided` 事件的 payload），调用方**没有**"改成什么"可填。界面因此必须在
 * 提交前把将应用的内容显示出来（`decorateCase` 的 `approval` 就是为它准备的），
 * 否则用户是在**盲操作**，而应用写下去的是 append-only 的版本历史。
 *
 * 服务端语义（由 `caseWriteError` 按状态码翻成人话）：
 *   · **409** —— 依据版本已变化（旧批准不能直接应用）或状态不允许 ⇒ 必须重新取数；
 *   · **400** —— 缺少批准快照 / 变更类别未登记：这是"请求本身不完整"，
 *                不是"业务规则挡下"，所以它**不留** `applied_rejected` 事件；
 *   · **403** —— 没有写权限（界面据 `capabilities` 显隐只是体验层）。
 */
function applyCase(caseId, body, idempotencyKey) {
  return request({
    url: BASE + '/exceptions/' + caseId + '/apply',
    method: 'POST',
    data: body,
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 受影响项的**候选**（登记 link 时要选一个具体目标）。
 *
 * 为什么必须去取候选而不是让用户手填编号：`target_id` 必须与案件**同属一张委托单**
 * （`_assert_link_target`，跨单一律 403）。手填编号等于让用户在盲猜，而猜错的反馈
 * 是一句 403 —— 他会以为是权限问题，而不是"这个编号不属于本单"。
 *
 * 两个取数各自复用既有端点，不为本页新开一个"候选清单"接口：
 *   · 任务 → `GET /entrust/tasks?assignment_id=`（**翻页取全**：这份候选要的是
 *     "这张单的任务全集"，不是"第一页" —— 缘由写在 `fetchTaskCandidates` 里）；
 *   · 成果 → `GET /entrust/assignments/{id}/artifacts`。
 * 两者都是**该委托的可见性**，与案件一致；不需要额外授权。
 */
function fetchTaskCandidates(assignmentId, size) {
  const pageSize = size || 100
  // 3 页护栏：任务数是"个位数"量级，正常情况下第 1 页就取完（端点的 `size` 上限是 100）。
  // 留着上限是为了**不无限翻**，而不是"允许截断" —— 真到 300 条以上，响应里的
  // `has_more` 仍为 true（事实留在数据里，不再是静默），只是这份候选不再是全集。
  const MAX_PAGES = 3
  const page = function (n) {
    return request({
      url: BASE + '/tasks',
      method: 'GET',
      data: { assignment_id: assignmentId, page: n, size: pageSize }
    })
  }
  const collect = function (resp, n, acc) {
    const items = acc.concat((resp && resp.items) || [])
    // ⚠️ 严格判 `=== true`：老后端没有这个字段时**不翻页**，也不臆断"还有更多"
    //    （与 `decorateAssignmentPlan` 对 `task_prerequisites_truncated` 的处置同口径）。
    if (!resp || resp.has_more !== true || n >= MAX_PAGES) {
      return Object.assign({}, resp || {}, { items: items })
    }
    return page(n + 1).then(function (next) {
      return collect(next, n + 1, items)
    })
  }
  return page(1).then(function (first) {
    return collect(first, 1, [])
  })
}

function fetchArtifactCandidates(assignmentId, size) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/artifacts',
    method: 'GET',
    data: { page: 1, size: size || 50 }
  })
}

/**
 * 候选清单 → 选择条用的行（`{key, target_kind, target_id, text, sub}`）。
 *
 * 两个端点的行形状不同（`task_id`/`title` vs `artifact_id`/`artifact_type`），
 * 但**选择条只要两列**：选谁、它是干什么的。归一化放在这里而不是页面里 ——
 * 页面各归一一次就会有第二份字段名映射。
 *
 * 成果的类型名来自**类型注册表**（`types`，即 `fetchArtifactTypes()` 的结果），
 * 本文件里**没有**能凭空翻译 `artifact_type` 的静态表 —— 所以取不到就显示原始代码，
 * 而不是编一个像样的中文名（未知值保持未知）。
 *
 * @param {object} tasks     `GET /entrust/tasks` 的响应
 * @param {object} artifacts `GET /entrust/assignments/{id}/artifacts` 的响应
 * @param {Array}  [types]   成果类型注册表 `[{code, label}]`；缺省则不翻译类型名
 */
function decorateCaseLinkTargets(tasks, artifacts, types, selfTarget) {
  const specs = types || []
  const typeLabel = function (code) {
    for (let i = 0; i < specs.length; i++) {
      if (specs[i] && specs[i].code === code) return specs[i].label || code
    }
    return code
  }
  // 「本委托货量」排在**最前**：它是这张单自己的属性，与"这单上有什么任务/成果"
  // 是两类东西。混在成果堆里，选错的人会以为自己在改某一版报价。
  //
  // `selfTarget` 缺省时不产出这一行（不是产出空行）：调用方还没拿到委托详情时，
  // 给一个点不动的候选比不给更糟。`quantityText` 也由调用方给 ——
  // 本层不自己去查货量，那是**另一条通道**（`GET /assignments/{id}`）。
  const selfRows = []
  if (selfTarget && selfTarget.assignment_id) {
    const sid = String(selfTarget.assignment_id)
    selfRows.push({
      key: 'assignment-' + sid,
      target_kind: 'assignment',
      target_id: sid,
      text: '本委托货量（委托 #' + sid + '）',
      sub: '受影响项是这张委托单本身：当前货量 ' + (selfTarget.quantityText || '未知')
    })
  }
  const taskRows = ((tasks && tasks.items) || []).map(function (t) {
    const id = t.task_id === null || t.task_id === undefined ? '' : String(t.task_id)
    return {
      key: 'task-' + id,
      target_kind: 'task',
      target_id: id,
      text: '任务 #' + id,
      sub: (TASK_TYPE_LABELS[t.task_type] || t.task_type || '') + ' · ' + (t.title || '未命名')
    }
  })
  const artRows = ((artifacts && artifacts.items) || []).map(function (a) {
    const id = a.artifact_id === null || a.artifact_id === undefined ? '' : String(a.artifact_id)
    return {
      key: 'artifact-' + id,
      target_kind: 'artifact',
      target_id: id,
      text: '成果 #' + id,
      sub: typeLabel(a.artifact_type || '') || '未命名成果'
    }
  })
  return selfRows.concat(taskRows, artRows)
}

// ─────────────────────────────────────────────────────────────────────────────
// 客户受理（写路径 · S1 / DEMO-1 §3.1–3.3）
// ─────────────────────────────────────────────────────────────────────────────
//
// ── 本切片补的是什么 ────────────────────────────────────────────────────
// 「发布货源」页的「委托发货」此前只跳到一个占位预览页（`pages/preview/preview`），
// **没有任何接口调用** —— 货主根本没能把委托交出去。本切片把它接上：
//
//     cargo --push--> intake --POST /assignments--> 草稿 --submit--> 待受理（submitted）
//
// ── 为什么提交目标不能拿 `/my-orgs` 顶上 ────────────────────────────────
// 提交校验读的是 `ent_entrustment`（我把委托授权给了谁），而 `/my-orgs` 读的是
// `ent_org_member`（我在谁那里有身份）。DR-0012：**归属 ≠ 权限边界**。
// 用成员关系渲染提交目标，就会给出一个"能选但必然 403"的选项。
// 所以本切片先补了只读端点 `GET /my-entrustments`（见 `DEMO-1-interface-delta.md` §3.1）。
//
// ── 幂等键为什么是**两把** ──────────────────────────────────────────────
// "创建 + 提交"是两个独立的写命令，各自需要一个键：一次网络抖动若让创建重发，
// 只带一把键会让第二次创建变成**另一张草稿**（键不同 = 新意图），库里就多出一张
// 没人认领的空壳。两把键由页面持有、各自绑定各自的意图，失败重试各自复用；
// 内容一旦改动，创建那一把必须作废（用户改了内容 = 新的草稿意图）。

/**
 * 拉取「我**授权出去**的组织」（UI-07 的**提交目标**数据源）。
 *
 * ⚠️ 与 `fetchMyOrgs()` **不是同一件事**，两者不可互相顶替：
 *   · `/my-orgs`         读 `ent_org_member` —— 「我**所在**的组织」；
 *   · `/my-entrustments` 读 `ent_entrustment` —— 「我**授权出去**的组织」。
 * 提交委托（`POST /assignments/{id}/submit`）校验的是后者。
 *
 * 服务端只返回**生效中**的授权（组织 active + 状态 active + 在时间窗内，
 * 且与提交门禁同口径），所以本清单**就是**可提交目标的全集 ——
 * 前端不得再自己过滤、补充或按 `current_role` 猜。
 */
function fetchMyEntrustments() {
  return request({ url: BASE + '/my-entrustments', method: 'GET' })
}

/**
 * 委托授权清单投影。
 *
 * ⚠️ 这里**不映射 `status` 的取值域**：服务端只返回生效中的授权，
 * 界面上那句"有效"是一个**常量**而不是一张映射表。若将来改为同时返回失效授权，
 * 必须先把 `ent_entrustment.status` 的镜像表登记进 CI（AGENTS.md §3.5），
 * **不得只在前端写一份**。
 */
function decorateEntrustment(row) {
  const data = row || {}
  const labels = (data.permissions || []).map(function (p) {
    return ORG_PERMISSION_LABELS[p] || p
  })
  return {
    entrustmentId:
      data.entrustment_id === null || data.entrustment_id === undefined
        ? ''
        : String(data.entrustment_id),
    orgId: data.org_id === null || data.org_id === undefined ? '' : String(data.org_id),
    orgName: data.org_name || '未命名组织',
    permissions: labels,
    permissionText: labels.join(' · '),
    grantedAt: data.granted_at || ''
  }
}

function decorateEntrustments(rows) {
  return (rows || []).map(decorateEntrustment)
}

/**
 * 决定「默认选中哪个提交目标」。
 *
 * 与 `pickOrg` 同一条纪律：**唯一选项直接选中，多个不猜**。
 * 猜错的后果是把委托提交到**另一个组织** —— 货主在界面上几乎不可能自己发现，
 * 远不如让用户明确选一次。
 *
 * @returns {{orgId:string, needPick:boolean}}
 */
function pickEntrustment(rows) {
  const list = rows || []
  if (!list.length) return { orgId: '', needPick: false }
  if (list.length === 1) return { orgId: String(list[0].orgId), needPick: false }
  return { orgId: '', needPick: true }
}

/**
 * 委托草稿请求体（`POST /assignments`）+ 前置校验。
 *
 * ## 数量**留空就是未知**，不是 0
 * PRD 5.1 要求"未知保持未知"。静默补 0 会让经理按"0 吨货"去报价 ——
 * 那是一个凭空造出来的事实，而且没人会怀疑它。所以空串一律**省略字段**，
 * 交给服务端存 NULL。
 *
 * ## 为什么字段级上限在这里也写一遍
 * 上限是服务端的（`AssignmentCreate`），这里写是为了**在发请求之前**给出可读提示；
 * 两侧不一致时的权威判定仍是服务端的 422，不会被这一层掩盖。
 *
 * @returns {{ok:boolean, errors:string[], body:object}}
 */
function assignmentDraftBody(form) {
  const f = form || {}
  const title = String(f.title || '').trim()
  const summary = String(f.cargo_summary || '').trim()
  const unit = String(f.quantity_unit || '').trim()
  const rawQty = f.quantity === null || f.quantity === undefined ? '' : String(f.quantity).trim()

  const errors = []
  if (!title) errors.push('请填一句话说明（例如：大连→上海 5 万吨煤炭，需代订舱）')
  else if (title.length > 128) errors.push('一句话说明超过 128 字')
  if (summary.length > 512) errors.push('货物说明超过 512 字')
  if (unit.length > 24) errors.push('数量单位不超过 24 字')

  let quantity = null
  if (rawQty) {
    // 与后端一致：非负、最多 3 位小数（`Decimal` 落库，不用浮点）
    if (!/^\d+(\.\d{1,3})?$/.test(rawQty)) {
      errors.push('数量只能填非负数字（最多 3 位小数），不确定就留空')
    } else {
      quantity = rawQty
    }
  }

  const body = { title: title }
  if (summary) body.cargo_summary = summary
  if (quantity !== null) body.quantity = quantity
  if (unit) body.quantity_unit = unit
  return { ok: errors.length === 0, errors: errors, body: body }
}

/**
 * 创建委托草稿（`POST /assignments`，幂等）。
 *
 * 草稿允许不完整（PRD §2.1 第 1 步）—— 本页把它当作"创建 + 提交"两步的第一步，
 * 而不是两个按钮：货主的心智是"把这张委托交出去"，中间那个纯草稿态对他没有意义。
 * 但**技术上**它确实是两条写命令，因此页面必须处理"建成了、提交没成"的中间态
 * （见 `intake.js` 的 `createdId`）。
 */
function createAssignment(body, idempotencyKey) {
  return request({
    url: BASE + '/assignments',
    method: 'POST',
    data: body,
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 提交委托到服务经营主体（`POST /assignments/{id}/submit`，幂等）。
 *
 * `expectedRevision` 必填：并发保护靠**乐观锁**（服务端条件更新），不靠界面禁点 ——
 * 界面禁点只能防同一台设备上的连点，防不住另一个终端。
 */
function submitAssignment(assignmentId, orgId, expectedRevision, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/submit',
    method: 'POST',
    data: { org_id: Number(orgId), expected_revision: Number(expectedRevision) },
    silent: true,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 委托写失败 → **可执行的结论**（与 `caseWriteError` 同构，话术按"委托"改）。
 *
 * 逐码分流而不是把服务端 `detail` 转手抛给用户：
 *   · **409** 状态/版本已变（草稿已被提交、或 `revision` 过期）→ 唯一正确的下一步是
 *     **重新取数**，所以话术必须包含"已重新读取"，而这句话只有页面能兑现；
 *   · **403** 对该组织没有生效授权 → 刷新**能**改变结论（授权可能刚被撤回），
 *     所以要说清"重新选择服务主体"，而不是让用户反复点同一个按钮；
 *   · **400** 字段/规则校验 → 服务端 `detail` 本身就是最准的说明，原样带上；
 *   · **404** 与读路径同码（开关关闭 / 不存在 / 非参与方**刻意同码**）→
 *     只能说"功能未开放"，**不能**替服务端下"这张委托不存在"这个结论；
 *   · **0**（无 httpStatus）→ 网络层，复用请求层的诊断文案。
 *
 * @returns {{kind:string, title:string, hint:string, conflict:boolean}}
 */
function assignmentWriteError(err) {
  const status = (err && err.httpStatus) || 0
  const detail = err && err.detail ? String(err.detail) : ''
  if (status === 409) {
    return {
      kind: 'conflict',
      title: '状态或版本已变化',
      conflict: true,
      hint: detail || '这张委托的当前状态已经变了。已重新读取，请确认后重来。'
    }
  }
  if (status === 403) {
    return {
      kind: 'denied',
      title: '当前不能提交到这个服务主体',
      conflict: false,
      hint: detail || '你对这个组织的委托授权可能已撤回或已过期。请重新选择服务主体。'
    }
  }
  if (status === 400) {
    return {
      kind: 'invalid',
      title: '按当前信息不能提交',
      conflict: false,
      hint: detail || '服务端校验未通过，请按提示修改后重试。'
    }
  }
  if (status === 404) {
    return {
      kind: 'notfound',
      title: '功能未开放或无权查看',
      conflict: false,
      hint: detail || '委托发货功能可能未开放，或这张委托不在你的可见范围内。'
    }
  }
  return {
    kind: 'network',
    title: '提交失败',
    conflict: false,
    hint: '网络层异常：请确认后端已启动、且没有代理拦截请求。已保留你填的内容，可直接重试。'
  }
}

// ── 会话 / 消息 / 作业（S2 首片：真实报价会话）────────────────────────────
//
// 后端这条链路（会话 / 消息 / 作业 / run / 接管 / 附件 / 提取）在 ENT-011 就已落库，
// 但前端**一个函数都没有** —— 会话屏因此只有成果卡、没有消息与作业（UI-03 缺一半）。
// 本段补的正是这个缺口。
//
// 三条纪律与既有各段一致：
//   1. 枚举表与后端取值域逐字对齐（AGENTS.md §3.5 ⇒ 由 verify_entrust_ui.js 交叉断言）；
//   2. 投影函数不接触 wx，CI 可在 Node 里直接驱动；
//   3. 写操作一律带 Idempotency-Key，由调用方生成并在重试时复用。
//
// ⚠️ **fixture 与真实模型必须分开标注**：`LLM_MOCK=true` 时后端走本地规则模板，
// 结构同构但不是真实模型输出。`mocked` 字段由后端给出，界面必须显示模式，
// **不得**把 fixture 结果当作模型质量的证据（DR-0005「未覆盖」第一条）。

/** 会话状态取值域 —— 与后端 `sessions.STATUS_*` 对齐 */
const SESSION_STATUS_LABELS = {
  active: '进行中',
  archived: '已归档'
}

/** 消息角色取值域 —— 与后端 `sessions.ROLE_*` 对齐 */
const MESSAGE_ROLE_LABELS = {
  user: '我',
  agent: 'Agent',
  system: '系统'
}

/** 消息来源取值域 —— 与后端 `sessions.SOURCE_*` 对齐（界面必须标注来源） */
const MESSAGE_SOURCE_LABELS = {
  manual: '手发',
  agent: 'Agent 产出',
  deterministic: '规则生成'
}

/** 作业状态取值域 —— 与后端 `agentjobs.STATUS_*` 对齐 */
const JOB_STATUS_LABELS = {
  queued: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消'
}

/**
 * 作业状态样式类 —— 与既有 `statusClass` 同口径：**返回完整类名串**（`chip chip-x`），
 * 模板里写 `class="{{item.statusClass}}"`。
 *
 * ⚠️ 取值必须落在 `app.wxss` 已定义的 `chip-*` 内（现测：muted / warn / success /
 * danger / purple）。在页面 wxss 里另起一份 `chip-ok` 就是第二份真相 ——
 * 全局那份改了颜色，本页不会跟着变。
 */
const JOB_STATUS_CLASS = {
  queued: 'chip chip-muted',
  running: 'chip chip-warn',
  succeeded: 'chip chip-success',
  failed: 'chip chip-danger',
  cancelled: 'chip chip-muted'
}

function sessionStatusLabel(status) {
  return SESSION_STATUS_LABELS[status] || status || '未知'
}

function messageRoleLabel(role) {
  return MESSAGE_ROLE_LABELS[role] || role || '未知'
}

function messageSourceLabel(source) {
  return MESSAGE_SOURCE_LABELS[source] || source || '未知'
}

function jobStatusLabel(status) {
  return JOB_STATUS_LABELS[status] || status || '未知'
}

function jobStatusClass(status) {
  return JOB_STATUS_CLASS[status] || 'chip chip-muted'
}

function _sid(v) {
  return v === null || v === undefined ? '' : String(v)
}

function decorateSession(row) {
  const data = row || {}
  return {
    sessionId: _sid(data.session_id),
    entrustmentId: _sid(data.entrustment_id),
    assignmentId: _sid(data.assignment_id),
    specialty: data.agent_specialty || '',
    specialtyLabel: data.agent_specialty_label || '通用会话',
    title: data.title || '未命名会话',
    status: data.status || '',
    statusLabel: sessionStatusLabel(data.status)
  }
}

function decorateMessage(row) {
  const data = row || {}
  return {
    messageId: _sid(data.message_id),
    seq: data.seq === null || data.seq === undefined ? 0 : Number(data.seq),
    role: data.role || '',
    roleLabel: messageRoleLabel(data.role),
    source: data.source || '',
    sourceLabel: messageSourceLabel(data.source),
    content: data.content || '',
    jobId: _sid(data.job_id),
    createdAt: data.created_at || ''
  }
}

/**
 * 作业投影。
 *
 * ⚠️ `envelope` 是**提案**，不是成果（AC-09）：它只有被 `adopt` 之后才成为成果。
 * 界面把它显示成"解析结果"会让人以为已经落库 —— 所以卡上必须写"提案"。
 */
/**
 * 来源引用取值域 —— 与后端 `runner.KIND_*` 一一对应。
 *
 * ⚠️ `attachment` 与 `attachment_text` **不是同一件事**：
 * 前者说"这个附件存在"，后者说"它的文本被读进来了"。
 * 界面必须分开显示 —— 把两者画成一个样子，等于替 Agent 把"读了"说成"有"。
 */
const SOURCE_KIND_LABELS = {
  assignment: '受理单',
  task: '任务',
  artifact: '成果',
  attachment: '附件',
  attachment_text: '附件文本',
  entrustment: '委托授权',
  operator_input: '操作者输入'
}

const SOURCE_KIND_ORDER = [
  'assignment',
  'task',
  'artifact',
  'attachment',
  'attachment_text',
  'entrustment',
  'operator_input'
]

function sourceKindLabel(kind) {
  const k = String(kind || '')
  if (!k) return '来源未知'
  // 未知取值照实回显，不折成某个已知标签（折了会让人以为来源类型只有这几种）
  return SOURCE_KIND_LABELS[k] || '未知来源（' + k + '）'
}

function decorateJob(row) {
  const data = row || {}
  const env = data.envelope || null
  // ⚠️ 键名是 **`artifact_proposals`**（见后端 `envelope.project_envelope_for_operator`
  // 的返回），不是 `proposals`。写成 `proposals` 不会报错，只会让"提案 N 条"
  // **永远是 0** —— 界面上看起来像"模型没给出任何提案"，而事实是读错了键。
  // 这条是靠 e2e 的"作业成功 ⇒ 页面必须列出提案"断言抓到的。
  const proposals = ((env && env.artifact_proposals) || []).map(function (p) {
    const one = p || {}
    const payload = one.payload || {}
    return {
      artifactType: one.artifact_type || '',
      typeLabel: one.artifact_label || one.artifact_type || '未命名提案',
      note: one.note || '',
      // ⚠️ `payload` 保留**原始值**：提交采纳时必须原样发回去。
      // 用下面 `rows[].value`（已格式化成字符串）去提交会把数值/数组写成字符串，
      // 而服务端拿它当字段值存 —— 那是一次静默的数据损坏。
      payload: payload,
      // 提案内容**逐字段列出**：合同 BP-02 要求"类型化提案（价格/单位/有效期/
      // 含与不含/来源/明确未知）"。只显示"提案 1 条"等于把要给人过目的东西藏起来，
      // 而"人工采纳"这件事的前提正是**人看过了内容**。
      // 中文标签复用成果页那一张表（`ARTIFACT_FIELD_LABELS`），不另造一份。
      rows: Object.keys(payload).map(function (k) {
        const raw = payload[k]
        const kind = _fieldKind(raw, '')
        return {
          key: k,
          label: artifactFieldLabel(k),
          value: kind === 'json' ? _structuredText(raw) : _scalarText(raw)
        }
      })
    }
  })
  return {
    jobId: _sid(data.job_id),
    sessionId: _sid(data.session_id),
    status: data.status || '',
    statusLabel: jobStatusLabel(data.status),
    statusClass: jobStatusClass(data.status),
    errorKind: data.error_kind || '',
    errorMessage: data.error_message || '',
    mocked: !!data.mocked,
    finishedAt: data.finished_at || '',
    proposalCount: proposals.length,
    proposals: proposals,
    /**
     * **来源**（HO 0917-3 裁定二第 3 条：页面必须显示来源与未核验警告）。
     *
     * 这一条补的是一个明确的产品缺口：此前经理人只看得到一个"采纳"按钮，
     * 看不见这份提案**引用了什么**、其中哪些**在服务端核不上**。
     * "有人工采纳按钮"不能替代"人能看见需要核对什么"。
     */
    sources: ((env && env.source_refs) || []).map(function (r) {
      const one = r || {}
      return {
        key: String(one.kind || '') + ':' + String(one.ref || ''),
        kind: one.kind || '',
        ref: one.ref || '',
        kindLabel: sourceKindLabel(one.kind),
        text: sourceKindLabel(one.kind) + ' #' + (one.ref || '?')
      }
    }),
    /**
     * 服务端**核对不上**的引用（`envelope.validate_envelope` 的产出，含嵌套
     * `findings[].source_refs`）。非空 ⇒ 界面必须警示，且采纳不等于"已核实"。
     */
    unverified: ((env && env.unverified_sources) || []).map(function (u) {
      const one = u || {}
      return {
        key:
          String(one.kind || '') +
          ':' +
          String(one.ref || '') +
          ':' +
          String(one.where || 'source_refs'),
        kind: one.kind || '',
        ref: one.ref || '',
        where: one.where || 'source_refs',
        kindLabel: sourceKindLabel(one.kind),
        text: sourceKindLabel(one.kind) + ' #' + (one.ref || '?') + '（' + (one.where || '') + '）'
      }
    }),
    /**
     * 能否采纳（**纯展示**，不判权）。
     *
     * 与 `canClaimAssignment` 同一条纪律：前端不假装知道当前身份有没有
     * `entrust:quote:create`（那取决于组织成员资格与授权，只有服务端知道）。
     * 写端 `adopt_job_proposal` 会独立复核权限与作业状态 ——
     * **隐藏按钮不等于放行**，这里少显示一个按钮只是少一次必然失败的点击。
     */
    canAdopt: data.status === 'succeeded' && proposals.length > 0,
    envelope: env,
    // 迟到写入作废时后端会给 lease_lost —— 界面必须能说"跑过但没生效"
    leaseLost: !!data.lease_lost
  }
}

/** 作业失败原因的一句话（失败必须**明确**，不能只写"失败"） */
function jobFailureText(job) {
  const j = job || {}
  if (j.status !== 'failed') return ''
  if (j.leaseLost) return '本次执行结果已作废（租约已被接管），未产生任何业务变更'
  const kind = j.errorKind || 'unknown'
  const msg = j.errorMessage ? '：' + j.errorMessage : ''
  return '失败类型 ' + kind + msg
}

/** 会话列表（`view=mine` 或 `org`；S2 首片用 mine + assignment_id 定位本单会话） */
function fetchSessions(options) {
  const o = options || {}
  const q = []
  q.push('view=' + encodeURIComponent(o.view || 'mine'))
  if (o.orgId) q.push('org_id=' + encodeURIComponent(o.orgId))
  if (o.assignmentId) q.push('assignment_id=' + encodeURIComponent(o.assignmentId))
  if (o.status) q.push('status=' + encodeURIComponent(o.status))
  q.push('page=' + encodeURIComponent(o.page || 1))
  q.push('size=' + encodeURIComponent(o.size || 20))
  return request({ url: BASE + '/sessions?' + q.join('&'), method: 'GET' })
}

/**
 * 创建会话。挂在委托授权下 —— 权限边界由授权链决定，
 * 所以**必须**先拿到 `entrustment_id`（页面从工作台进来时只有委托单号）。
 */
function createSession(entrustmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/entrustments/' + entrustmentId + '/sessions',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 会话详情（含消息时间线）。可见性由服务端判定，不可见 404。 */
function fetchSession(sessionId) {
  return request({ url: BASE + '/sessions/' + sessionId, method: 'GET' })
}

/**
 * 会话上下文：**为这单开会话该用哪条委托授权**。
 *
 * ⚠️ 这个端点的存在，就是为了让页面**不必猜** `entrustment_id`。
 * 建会话的路径参数要求先知道授权 id，而 `/my-orgs` 只回成员身份、
 * `/my-entrustments` 只回货主自己授权出去的授权 —— 经理两边都拿不到本单那一条。
 * 曾经想在前端"取第一个组织"顶上，那等于拿一个**恰好长得像**的 id
 * 去撞权限（撞不中就表现为"能进页面但建不了会话"）。
 *
 * 返回值：`{assignment_id, org_id, entrustment_id, note}`。
 * `entrustment_id` 为 `null` 时**不是**"禁止"，是"定位不到唯一一条"，
 * 原因在 `note` 里（未指定组织 / 无生效授权 / 多条需显式指定）。
 */
function fetchSessionContext(assignmentId) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/session-context',
    method: 'GET'
  })
}

/** 追加一条操作者消息（角色固定 user，防伪造 agent 消息） */
function appendMessage(sessionId, content, idempotencyKey) {
  return request({
    url: BASE + '/sessions/' + sessionId + '/messages',
    method: 'POST',
    data: { content: content },
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 本单附件（引用用；提取状态由服务端给） */
function fetchEntrustmentAttachments(entrustmentId) {
  return request({
    url: BASE + '/entrustments/' + entrustmentId + '/attachments',
    method: 'GET'
  })
}

/**
 * 附件提取状态取值域 —— 与后端 `attachments.EXTRACT_*` 一一对应（七态）。
 *
 * ⚠️ 这张表**必须覆盖全部取值**。旧写法只有"done / 其它"两支，于是
 * `needs_transcription`（图片扫描件等人工转录）与 `failed`（提取真的坏了）
 * 在界面上**长得一模一样**，都显示"未提取" —— 而这两种状态该做的事完全不同：
 * 前者要找人转录，后者要排查文件或重抽。少一档就是把两件事混成一件。
 */
const EXTRACT_STATUS_LABELS = {
  not_requested: '未提取',
  pending: '提取排队中',
  running: '提取中',
  done: '已提取文本',
  failed: '提取失败',
  unsupported: '不支持该格式',
  needs_transcription: '需人工转录'
}

/** 与后端取值域完全一致的顺序（断言用，勿随意增删） */
const EXTRACT_STATUS_ORDER = [
  'not_requested',
  'pending',
  'running',
  'done',
  'failed',
  'unsupported',
  'needs_transcription'
]

function extractStatusLabel(status) {
  const s = String(status || '')
  if (!s) return '状态未知'
  // 未知取值**照实回显**，不折成某个已知标签：把后端新加的状态显示成"未提取"，
  // 会让人以为是"还没点提取"，而真因是前端没跟上取值域。
  return EXTRACT_STATUS_LABELS[s] || '未知状态（' + s + '）'
}

function _sizeText(bytes) {
  const n = Number(bytes)
  if (!isFinite(n) || n < 0) return ''
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}

/**
 * 附件文本来源取值域 —— 与后端 `attachments.TEXT_SOURCE_*` 一一对应。
 *
 * 为什么界面要关心"这段文本是谁写的"：**机读提取可以重抽，人工转录不能**。
 * 转录内容不可再生成（扫描件重抽只会得到"需要转录"），而重抽失败时后端还会
 * 删掉旧文本 ⇒ 一次失败的重抽足以让人的劳动凭空消失。界面因此在重抽前必须
 * 先问一句。
 */
const TEXT_SOURCE_LABELS = {
  extractor: '机读提取',
  manual_transcription: '人工转录'
}

const TEXT_SOURCE_ORDER = ['extractor', 'manual_transcription']

function textSourceLabel(source) {
  const s = String(source || '')
  if (!s) return '无文本'
  return TEXT_SOURCE_LABELS[s] || '未知来源（' + s + '）'
}

function decorateAttachment(row) {
  const data = row || {}
  const status = String(data.extract_status || '')
  // ⚠️「**已上传**」≠「**Agent 能看到内容**」：只有提取完成（`done`）的附件
  // 才有文本进 Agent 的来源目录（见后端 `agentjobs._attach_text_excerpts`）。
  // 未提取的附件对 Agent 而言只是"一个文件名"。`canReference` 就是这条区分
  // 在界面上的落点 —— 少了它，用户会以为"传上去了 Agent 就该读到"。
  const hasText = status === 'done'
  const textSource = String(data.text_source || '')
  const manual = textSource === 'manual_transcription'
  return {
    attachmentId: _sid(data.attachment_id),
    name: data.name || data.filename || '未命名附件',
    sizeText: _sizeText(data.size_bytes),
    contentType: data.content_type || '',
    extractStatus: status,
    extractLabel: extractStatusLabel(status),
    hasText: hasText,
    canReference: hasText,
    /** 当前文本的来源（机读提取 / 人工转录）；没文本时为空 */
    textSource: textSource,
    textSourceLabel: textSourceLabel(textSource),
    /**
     * 重抽会不会**覆盖掉人写的内容**。界面据此先出确认条：
     * 覆盖人工转录不可恢复（转录内容不可再生成，且失败的提取还会删文本）。
     */
    overwritesManualText: manual,
    /**
     * 能不能走人工转录。只在"有内容但拿不到机读文本"时才有意义：
     * `needs_transcription`（扫描件/图片）与 `unsupported`（格式不支持）。
     * `failed` 是文件坏了，先换文件，转也白转。
     */
    canTranscribe: status === 'needs_transcription' || status === 'unsupported',
    referenceHint: hasText
      ? 'Agent 会读到这份附件的文本'
      : status === 'needs_transcription'
        ? '图片/扫描件须先人工转录，之后才能被引用'
        : status === 'failed'
          ? '提取失败：' + (data.extract_error || '原因未记录')
          : status === 'unsupported'
            ? '该格式不支持提取，Agent 只能看到文件名'
            : '尚未提取文本，Agent 只能看到文件名'
  }
}

/**
 * 上传一份附件（multipart）。
 *
 * 为什么不能复用 `request()`：上传走的是另一套 API（`wx.uploadFile` 自己拼
 * multipart），它**不带鉴权头**、也**不解析 JSON 响应**（`res.data` 是字符串）。
 * 这两件事漏掉任一件的表现分别是 401 与"200 却什么字段都取不到"。
 *
 * 归属**恰好给一个**：`entrustmentId`（委托授权，参与方协作）或
 * `assignmentId`（私有未绑定草稿）。后端对"都不给"和"都给"都是 400 ——
 * 归属含糊的附件没有可见性规则可言，所以这里**不替它挑默认值**。
 */
function uploadAttachment(filePath, opts, idempotencyKey) {
  const o = opts || {}
  return new Promise(function (resolve, reject) {
    const form = {}
    if (o.entrustmentId) form.entrustment_id = String(o.entrustmentId)
    else if (o.assignmentId) form.assignment_id = String(o.assignmentId)
    const header = {}
    if (idempotencyKey) header['Idempotency-Key'] = idempotencyKey
    const token = getToken()
    if (token) header.Authorization = 'Bearer ' + token
    wx.uploadFile({
      url: BASE_URL + BASE + '/attachments',
      filePath: filePath,
      // 字段名必须是 `file`：后端按 `File()` 取它，换个名字就是 422
      name: 'file',
      formData: form,
      header: header,
      success(res) {
        let data = null
        try {
          data = JSON.parse(res.data)
        } catch (e) {
          data = null
        }
        if (res.statusCode >= 200 && res.statusCode < 300 && data) {
          resolve(data)
          return
        }
        const detail = (data && data.detail) || '上传失败'
        const err = new Error(String(detail))
        err.httpStatus = res.statusCode
        err.detail = String(detail)
        reject(err)
      },
      fail(err) {
        reject(err)
      }
    })
  })
}

/**
 * 触发附件文本提取（同步）。
 *
 * **可重复调用**（"重抽"就是同一个动作）：重复提取覆盖同一份文本，
 * 不产生多份互相矛盾的结果，所以不需要 `?refresh=true` 之类的开关。
 *
 * ⚠️ `opts.acknowledgeTranscriptionOverwrite` 是"我知道这会覆盖人工转录"的显式确认。
 * 该附件当前的文本来自人工转录时，后端**默认拒绝**（409）—— 转录内容不可再生成，
 * 而失败的提取还会删掉旧文本。确认由**页内确认条**收集，不由原生弹层（弹层不在
 * 渲染树里，关键路径拿不到设备证据）。
 */
function extractAttachment(attachmentId, idempotencyKey, opts) {
  const o = opts || {}
  const suffix = o.acknowledgeTranscriptionOverwrite
    ? '?acknowledge_transcription_overwrite=true'
    : ''
  return request({
    url: BASE + '/attachments/' + attachmentId + '/extract' + suffix,
    method: 'POST',
    data: {},
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 提交**人工转录**文本（图片/扫描件的降级通道）。
 *
 * 转录完成后文本来源是 `manual_transcription` —— 它会显示在附件行上，
 * 并让后续"重抽"变成需要确认的动作。
 */
function transcribeAttachment(attachmentId, text, idempotencyKey) {
  return request({
    url: BASE + '/attachments/' + attachmentId + '/transcription',
    method: 'POST',
    data: { text: text },
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 提交作业（**不执行**；执行要再调 runJob —— 两步分开是刻意的） */
function submitJob(sessionId, body, idempotencyKey) {
  return request({
    url: BASE + '/sessions/' + sessionId + '/jobs',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 推进一次作业（worker 单步）。
 *
 * ⚠️ 本端点**不要求** Idempotency-Key —— 它的幂等来自作业状态机与租约：
 * 重复调用会被判为"已结束"或"租约仍被持有"而 409，连一次尝试都不会多消耗。
 */
function runJob(jobId) {
  return request({ url: BASE + '/agent/jobs/' + jobId + '/run', method: 'POST', data: {} })
}

function fetchJob(jobId) {
  return request({ url: BASE + '/agent/jobs/' + jobId, method: 'GET' })
}

/** 作业列表（本单全部作业，用于"离开重进仍可恢复"） */
function fetchJobs(options) {
  const o = options || {}
  const q = []
  if (o.sessionId) q.push('session_id=' + encodeURIComponent(o.sessionId))
  if (o.assignmentId) q.push('assignment_id=' + encodeURIComponent(o.assignmentId))
  q.push('page=' + encodeURIComponent(o.page || 1))
  q.push('size=' + encodeURIComponent(o.size || 20))
  return request({ url: BASE + '/agent/jobs?' + q.join('&'), method: 'GET' })
}

/** 采纳提案为成果（人工发起；payload 是**人工确认过**的内容） */
function adoptJobProposal(jobId, body, idempotencyKey) {
  return request({
    url: BASE + '/agent/jobs/' + jobId + '/adopt',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}


// ─────────────────────────────────────────────────────────────────────────────
// 对客发布与客户响应（S3 纵向切片 / BP-03 第 4–10 条；D1-07 / D1-11）
// ─────────────────────────────────────────────────────────────────────────────
//
// 这一片把「经理发布指定版本 → 货主查看冻结内容 → 接受／拒绝 → 经理看到响应」
// 接到界面上。此前这三步只能用 API 走通，按 HO 的口径那**不算"按业务结果可演示"**。
//
// ── 四条必须写在界面上的语义（不靠后端默默保证）────────────────────────
//
// 1. **客户看到的是发布那一刻冻结的内容**，不是"这个成果的最新版"。
//    所以客户侧**一律**渲染 `release.content`（服务端投影的结果），**绝不**去读成果本体 ——
//    读成果会把"客户看到的"与"现在的最新版"混成一件事，而这两者在并发编辑下不同。
// 2. **数据来源标注要显示**（BP-03 第 9 条）：真实模型 / 演示合成 / 人工录入 / **未标注**。
//    `unknown` **照实显示成"来源未标注"**，不折成"人工录入"：折了就等于替审计做了判断，
//    而"不知道"与"不是模型产的"是两句不同的话。
// 3. **签署是样张模式**（`labeled_sample`，合同 §10.1 第 7 步）：界面上必须说明这次确认
//    是在演示语境下取得的，不是一个已生效的法律签署 —— 不标注就会被下游当成真签署。
// 4. **响应只有货主本人能做**。经理看到的是"客户响应了什么"，不是"我可以代他确认"
//    —— 后端对经理返回 **403**（不是 404：他看得见，只是无权）。界面按同一条口径分工。

/** 发布状态（后端 `offers.STATUS_*`）。 */
const OFFER_STATUS_META = {
  released: { label: '待客户确认', tone: 'warn' },
  withdrawn: { label: '已撤回', tone: 'muted' },
  superseded: { label: '已被新版本取代', tone: 'muted' }
}

/** 与后端取值域完全一致的顺序（断言用，勿随意增删） */
const OFFER_STATUS_ORDER = ['released', 'withdrawn', 'superseded']

function offerStatusLabel(status) {
  const s = String(status || '')
  if (!s) return '未知状态'
  const meta = OFFER_STATUS_META[s]
  // 未知取值照实回显，不折成某个已知标签
  return meta ? meta.label : '未知状态（' + s + '）'
}

function offerStatusClass(status) {
  const meta = OFFER_STATUS_META[String(status || '')]
  return 'chip chip-' + (meta ? meta.tone : 'muted')
}

/**
 * 数据来源标注（后端 `offers.ORIGIN_*`）。键必须覆盖后端全部取值。
 *
 * `unknown` 的文案是"**来源未标注**"而不是"未知"：后者听起来像系统坏了，
 * 而事实是"这条记录没有标注行"—— 该做的是去补记录，不是报故障。
 */
const DATA_ORIGIN_LABELS = {
  live: '真实模型调用',
  synthetic: '演示用合成数据',
  manual: '人工录入',
  unknown: '来源未标注'
}

const DATA_ORIGIN_ORDER = ['live', 'synthetic', 'manual', 'unknown']

function dataOriginLabel(mode) {
  const m = String(mode || '')
  if (!m) return DATA_ORIGIN_LABELS.unknown
  return DATA_ORIGIN_LABELS[m] || '未标注（' + m + '）'
}

/** 客户响应的**两层含义**要分开说清：决定是什么 + 依据是哪一版。 */
const OFFER_DECISION_LABELS = { accept: '接受', reject: '拒绝' }

function offerDecisionLabel(decision) {
  const d = String(decision || '')
  if (!d) return ''
  return OFFER_DECISION_LABELS[d] || '未知响应（' + d + '）'
}

/** 签署证据模式（后端 `offers.SIGNATURE_MODE_LABELED_SAMPLE`）。 */
const SIGNATURE_MODE_LABELS = { labeled_sample: '样本签署（演示语境）' }

function signatureModeLabel(mode) {
  const m = String(mode || '')
  if (!m) return '未标注签署模式'
  return SIGNATURE_MODE_LABELS[m] || '未知签署模式（' + m + '）'
}

/**
 * 签署模式的**一句解释**。
 *
 * 为什么必须常驻显示而不是只在首次提示：这一行决定了"这次确认算不算数"。
 * 把它藏进提示气泡里，界面上就只剩下"已接受"三个字 —— 而看的人会以为
 * 已经拿到了一份可执行的法律确认。
 */
function signatureModeHint(mode) {
  return String(mode || '') === 'labeled_sample'
    // ⚠️ 文案里**不得出现 Markdown 星号**：这里是 WXML 文本节点，`**x**` 会原样渲染成
    //    「**样本签署**」四个多余字符 —— 这是展示缺陷，且不会有任何静态检查拦住它。
    ? '本次客户确认按演示口径记录为「样本签署」，不作为已生效的法律签署'
    : ''
}

/** 对客成果类型的中文名（客户侧不取注册表，故在此内置；与后端注册表取值域对应）。 */
const OFFER_ARTIFACT_TYPE_LABELS = {
  customer_quote: '对客报价',
  quote_parsed: '报价解析稿',
  capacity_confirmation: '运力确认',
  contract_review: '合同核对稿'
}

function offerArtifactTypeLabel(code) {
  const c = String(code || '')
  if (!c) return '报价'
  return OFFER_ARTIFACT_TYPE_LABELS[c] || c
}

// ── 取数 ────────────────────────────────────────────────────────────────────

/** 客户侧：我（货主本人）收到的发布。归属由服务端从登录身份推导，不接受客户端指定。 */
function fetchMyOfferReleases() {
  return request({ url: BASE + '/my-offer-releases', method: 'GET' })
}

/** 经理侧：该委托授权下的发布记录（含客户快照、来源门槛与响应）。 */
function fetchEntrustmentOfferReleases(entrustmentId) {
  return request({
    url: BASE + '/entrustments/' + entrustmentId + '/offer-releases',
    method: 'GET'
  })
}

/** 单条发布：服务端按调用者身份选投影（客户投影 / 经理投影）。 */
function fetchOfferRelease(releaseId) {
  return request({ url: BASE + '/offer-releases/' + releaseId, method: 'GET' })
}

// ── 写命令 ──────────────────────────────────────────────────────────────────

/**
 * 发布**指定的那一个**版本。
 *
 * `body` 必须带 `artifact_id` 与 `revision_no` —— 后端**没有**"不传版本就发最新"
 * 的分支。界面因此也只能从"某一行的版本"发起，而不是从"这份成果"发起。
 */
function releaseOffer(entrustmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/entrustments/' + entrustmentId + '/offer-releases',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 客户接受／拒绝该已发布版本。只有货主本人能调（经理会拿到 403）。 */
function respondOffer(releaseId, body, idempotencyKey) {
  return request({
    url: BASE + '/offer-releases/' + releaseId + '/responses',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 显式撤回发布。理由必填；已被客户响应的发布撤不回来（后端 409）。 */
function withdrawOffer(releaseId, body, idempotencyKey) {
  return request({
    url: BASE + '/offer-releases/' + releaseId + '/withdraw',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

// ── 运力确认与有效期（S3；BP-03 第 2/3 条 / D1-06 / 合同 §10.1 第 5 步）────
//
// ⚠️ 这一组端点**没有客户面**：后端 `capacity_api` 整组走 `assert_can_view_org` /
// `assert_can_write_entrustment`，**不设货主旁路**。理由（后端模块文档逐条写着）：
// 候选行带**承运人**与**供应商单价**、确认行带 `agreed_amount`/`supplier`、
// 逐规则判定里还有需求量与缺口吨数。⇒ 本组取数**只该在经理侧**调用；
// 客户了解商业承诺的渠道是发布那一刻的**白名单快照**，不是这里。

/** 候选状态取值域（⇄ `capacity.CANDIDATE_STATUS_*`）。 */
const CANDIDATE_STATUS_ORDER = ['candidate', 'confirmed', 'withdrawn']
const CANDIDATE_STATUS_LABELS = {
  candidate: '候选',
  confirmed: '已确认运力',
  withdrawn: '已作废'
}

/**
 * 规则码 → 中文标签（⇄ `capacity.RULE_*`）。
 *
 * ⚠️ **本表不承担顺序**：后端 `ALL_RULE_CODES` 的顺序就是评估顺序与展示顺序，
 * 且 409 响应体里每条判定都带 `seq` —— 按 `seq` 排，不按这里书写的先后排。
 */
const CAPACITY_RULE_LABELS = {
  demand_known: '需求口径',
  evidence: '证据',
  validity: '有效期',
  capacity: '容量'
}

/** 判定结果（⇄ `capacity.OUTCOME_*`）。 */
const CAPACITY_OUTCOME_LABELS = { pass: '通过', fail: '不通过' }

/**
 * 证据类别取值域（⇄ `registry.ALL_EVIDENCE_KINDS`）。
 *
 * 与 `CAPACITY_RULE_LABELS` 同一取向：这张表**只做展示与选择**，
 * 判定（`evidence` 规则）由后端跑，写端 `record_candidate` 还会按取值域拒掉表外的值。
 * 页面把类别做成**页内选择条**而不是自由输入 —— 自由输入能填出必然 400 的值，
 * 而"类别写错了"与"随便填了个类别"在库里的形状是一样的（BP-03 第 3 条
 * 要的是「类别 + **引用**」，两者缺一都不算 identified evidence）。
 */
const CAPACITY_EVIDENCE_ORDER = [
  'document',
  'photo',
  'email',
  'receipt',
  'contract',
  'payment',
  'confirmation'
]
const CAPACITY_EVIDENCE_LABELS = {
  document: '单据',
  photo: '照片',
  email: '邮件',
  receipt: '回单',
  contract: '合同',
  payment: '付款凭证',
  confirmation: '确认函'
}

function capacityEvidenceLabel(kind) {
  const k = String(kind == null ? '' : kind)
  return CAPACITY_EVIDENCE_LABELS[k] || k
}

/** 候选状态标签；**未知取值照实回显**（缺一个就显示成"未知状态"是更坏的选择）。 */
function candidateStatusLabel(status) {
  const k = String(status == null ? '' : status)
  return CANDIDATE_STATUS_LABELS[k] || k
}

/** 规则标签；未知码照实回显（后端新增规则时，界面至少看得见它存在）。 */
function capacityRuleLabel(code) {
  const k = String(code == null ? '' : code)
  return CAPACITY_RULE_LABELS[k] || k
}

function capacityOutcomeLabel(outcome) {
  const k = String(outcome == null ? '' : outcome)
  return CAPACITY_OUTCOME_LABELS[k] || k
}

/**
 * 候选行的展示装饰。
 *
 * ⚠️ 数值**已经是定长文本**（后端 `_row_to_candidate` 归一：吨位 3 位、单价 4 位），
 * 本函数**原样显示**、不做 `Number()` 再格式化 —— 那会引入第二份"数值口径"，
 * 且把 `45.0000` 美化成 `45` 会让界面与库里存的字符串不一致。
 */
function decorateCapacityCandidate(c) {
  const row = c || {}
  const vessels = Number(row.vessel_count || 0)
  const partial = !!row.allows_partial_load
  const status = String(row.status == null ? '' : row.status)
  const evKind = row.evidence_kind ? capacityEvidenceLabel(row.evidence_kind) : ''
  const evRef = row.evidence_ref ? String(row.evidence_ref) : ''
  return {
    /**
     * ⚠️ **一律 `String()`**：模板里跟它比的那个键（`capConfirmKey`）来自 `dataset`
     *    并经 `String()` 归一（`onOpenCapConfirm`），**恒为字符串**。确认条用的是
     *    `wx:if="{{capConfirmKey === item.candidateId}}"`，严格比较 ——
     *    这里若原样透传 API 的 `int`（`CapacityCandidateOut.candidate_id: int`），
     *    就**恒不相等** ⇒ 确认条整块不渲染：点「确认这一条」界面上什么都不会出现，
     *    范围框 / 提交键在渲染树里根本不存在。
     *    设备走查 ㊺ 章 2026-09-17 实测：内部状态键已置上、三个锚点命中数全为 0，
     *    而四个前端静态门禁与 e2e 全绿（它们查得到模板里有 `data-df`，查不到运行时类型）。
     *    与 `taskOpenKey === item.key` 同一条口径（`WORKBENCH_SLOTS` 的 key 本就是字符串）。
     */
    candidateId: row.candidate_id == null ? '' : String(row.candidate_id),
    carrier: String(row.carrier == null ? '' : row.carrier),
    vesselName: row.vessel_name ? String(row.vessel_name) : '',
    capacityText: String(row.capacity_tonnes == null ? '' : row.capacity_tonnes),
    vesselCount: vessels,
    /**
     * 装载口径必须**看得见**：夹具层面已经证明，只写"900 吨"没有判据价值 ——
     * 若允许拆批或多船承运，950 吨**未必**装不下。
     */
    loadBasis:
      (vessels > 1 ? vessels + ' 船承运' : '单船承运') + '·' + (partial ? '允许拆批' : '不拆批'),
    rateText:
      row.rate == null ? '' : String(row.rate) + (row.rate_unit ? ' 元/' + row.rate_unit : ''),
    currency: row.currency ? String(row.currency) : '',
    validUntil: row.valid_until ? String(row.valid_until) : '',
    /**
     * 证据：BP-03 第 3 条要 `identified evidence` = **类别 + 引用**。
     * 只有类别时界面必须显示"缺引用"，否则"有单据"与"有这一份单据"看起来一样。
     */
    evidenceKind: row.evidence_kind ? capacityEvidenceLabel(row.evidence_kind) : '',
    evidenceRef: row.evidence_ref ? String(row.evidence_ref) : '',
    evidenceMissingRef: !!row.evidence_kind && !row.evidence_ref,
    /**
     * 证据这一格必须**三态可分**：有类别且有引用 / 有类别缺引用 / 未登记。
     * 折成两态（有 / 无）会让"有单据"与"有这一份单据"看起来一样 ——
     * 而 BP-03 第 3 条要的正是「类别 + 引用」两件齐全，`evidence` 规则也这么判。
     * 与 `loadBasis` 同一条取向：**判不了的那一格必须自己说话**。
     */
    evidenceText: row.evidence_kind
      ? evKind + (row.evidence_ref ? ' · ' + evRef : '（缺引用）')
      : '未登记',
    evidenceCls: row.evidence_kind && row.evidence_ref ? 'field-value' : 'cap-warn',
    status: status,
    statusLabel: candidateStatusLabel(status),
    statusClass: CANDIDATE_STATUS_ORDER.indexOf(status) >= 0 ? 'cap-' + status : 'cap-unknown',
    /** 只有 `candidate` 可被确认：`confirmed` 已确认过（`UNIQUE(candidate_id)`），`withdrawn` 已作废。 */
    confirmable: status === 'candidate'
  }
}

/**
 * 「两家可比」的**并排形态**：列 = 候选，行 = 维度（吨位 / 装载口径 / 单价 / 有效期 / 证据）。
 *
 * 为什么需要它：候选清单是纵向卡片，两家以上时要靠上下滚动 + 记忆对齐 ——
 * 而"这两家在哪一点上不同"正是**要不要选它**的判断依据，纵向列表恰恰把这条信息拆散了。
 * 卡片头那句「两家可比」在只有纵向列表时是**没有载体的承诺**。
 *
 * ⚠️ 只标「有差异」，**不标「哪条更好」**：哪一条更合适取决于货量、时效、能不能拆批，
 *    那是**方案判断**，不是投影能算出来的。给它一个"推荐"就是在替用户做商业决定 ——
 *    与 `capacity.list_candidates` 不按吨位排序是同一条取向（"哪一种排序更对"是方案判断，
 *    后端只保证清单稳定、可复现）。
 * ⚠️ 行的顺序**固定为定义序**，不按"有差异"重排：排序会随数据变，于是同一份数据
 *    两次打开表长得不一样 —— 与上面同一条取向（顺序稳定比"看起来聪明"重要）。
 * ⚠️ 只在 **≥2 条**时产出：一条候选没有"并排"可言，渲染出来只是白占一屏。
 *    返回空数组 ⇒ 模板 `wx:if="{{capCompareRows.length}}"` 自然不渲染（不需要额外的布尔量）。
 * ⚠️ `cells` 的每一项是**对象**（带 `i` = 列下标）而不是裸字符串：模板内层 `wx:for` 的
 *    `wx:key` 必须唯一，而两列的取值**可能真的相同**（比如两家都"未登记"证据）——
 *    拿取值当 key 会撞，拿列下标不会。这是模板约束，不是数据形状的偏好。
 *
 * ⚠️ 空值参与比较：「这家没写有效期」本身就是差异，不该被当成"没这一项"跳过。
 *    这与候选卡片的三态证据是同一条取向 —— **判不了的那一格必须自己说话**。
 */
function buildCapacityComparison(candidates) {
  const rows = Array.isArray(candidates) ? candidates : []
  if (rows.length < 2) return { cols: [], rows: [] }

  const cols = rows.map(function (c) {
    return {
      candidateId: c.candidateId,
      /**
       * 列头 = 承运人 · 船名。两家的承运人**可能同名**（同一条船在不同代理名下），
       * 所以列头不承担"分清哪一列"的全部责任 —— 状态标签与操作区仍以卡片为准。
       */
      head: String(c.carrier || '') + (c.vesselName ? ' · ' + c.vesselName : '')
    }
  })

  const dims = [
    {
      key: 'capacity',
      label: '运力',
      pick: function (c) {
        return c.capacityText ? c.capacityText + ' 吨' : ''
      }
    },
    {
      key: 'loadBasis',
      label: '装载口径',
      pick: function (c) {
        return String(c.loadBasis || '')
      }
    },
    {
      key: 'rate',
      label: '单价',
      pick: function (c) {
        return String(c.rateText || '') + (c.currency ? ' · ' + c.currency : '')
      }
    },
    {
      key: 'validUntil',
      label: '有效期至',
      pick: function (c) {
        return String(c.validUntil || '')
      }
    },
    {
      key: 'evidence',
      label: '证据',
      pick: function (c) {
        return String(c.evidenceText || '')
      }
    }
  ]

  const out = dims.map(function (d) {
    const texts = rows.map(d.pick)
    const first = texts[0]
    let diff = false
    texts.forEach(function (t) {
      if (t !== first) diff = true
    })
    return {
      key: d.key,
      label: d.label,
      diff: diff,
      cells: texts.map(function (t, i) {
        return { i: i, text: t }
      })
    }
  })
  return { cols: cols, rows: out }
}

/**
 * 409 响应体里的逐规则判定 → 展示行（**含通过的**）。
 *
 * 后端刻意把**全部**判定都回（通过的也在内）：只回没过的会让人改完再撞下一条；
 * 而通过的规则也带比较值，那是"这条规则确实跑过"的证据。
 * ⇒ 前端**不得**过滤掉通过项 —— 那等于把后端特意给的信息又扔掉。
 * 展示顺序 = **评估顺序**：按 `seq` 排（`seq` 缺失才退回本地下标）。
 * 后端目前按序发出，但**排一次**才算把这条不变量写在代码里 —— 依赖"后端恰好有序"
 * 是一种只在别人改动时才暴露的假设，而那时症状是"判定顺序看不懂"，不是报错。
 */
function capacityRuleRows(detail) {
  const d = detail || {}
  const list = Array.isArray(d.rule_checks) ? d.rule_checks : []
  return list
    .map(function (r, i) {
      const row = r || {}
      const outcome = String(row.outcome || '')
      return {
        seq: row.seq == null ? i + 1 : Number(row.seq),
        ruleCode: String(row.rule_code || ''),
        ruleLabel: capacityRuleLabel(row.rule_code),
        outcome: outcome,
        outcomeLabel: capacityOutcomeLabel(outcome),
        passed: outcome === 'pass',
        /** 通过 / 不通过**必须在界面上分得开**：只给一条灰字，读者会以为全是通过。 */
        outcomeClass: outcome === 'pass' ? 'cap-rule-pass' : 'cap-rule-fail',
        detail: String(row.detail || '')
      }
    })
    .sort(function (a, b) {
      return a.seq - b.seq
    })
}

// ── 取数（均为既有端点，本片只接线；**经理侧**）────────────────────────────

/**
 * 确认行的展示装饰。
 *
 * ⚠️ 这里显示的 `capacityText` / `validUntil` / `evidence*` **全部来自确认行** ——
 * 那是确认那一刻的**冻结副本**，不是候选行现在的值（候选行可以被改写，
 * 而确认是一条已经作出的商业事实）。想看"现在还是不是这样"，
 * 用 `recheckCapacityConfirmation()`；界面把两者并排显示，就是为了让
 * 「改过之后不再成立」看得见（D1-09 那句话的可见形态）。
 *
 * ⚠️ `agreedScope`（对应下面的 `scopeText`）的事实来源是这次确认产出的**成果版本** ——
 * 确认表上**没有**这一列（有意的：同一个事实在库里只留一份）。服务端按冻结在确认行上的
 * `artifact_revision_id` 投影（`capacity._scope_from_payload`），前端只搬运、不推导。
 * ⇒ 界面上显示的范围，与成果里写着的那一版**永远是同一份**，不需要任何同步动作。
 * （此前这里记的是"口径缺口：读模型不带范围"；那一缺口已落地。）
 */
function decorateCapacityConfirmation(c) {
  const row = c || {}
  const ruleRows = capacityRuleRows(row)
  const vessels = Number(row.vessel_count || 0)
  const partial = !!row.allows_partial_load
  let passed = 0
  ruleRows.forEach(function (r) {
    if (r.passed) passed += 1
  })
  return {
    /**
     * ⚠️ 同 `decorateCapacityCandidate.candidateId`：模板里跟它比的是 `capRecheckId`，
     *    而那是 `onCapRecheck` 里 **`String(id)`**（dataset 值）归一出来的字符串。
     *    `wx:if="{{capRecheckId === item.confirmationId && capRecheck}}"` 是严格比较，
     *    这里给 `int` 就恒假 ⇒ 「复算这条确认」点下去、请求也发了，**判定表却不出现**
     *    （页面看着像"复算了但没结果"）。同一轮 ㊺ 章走查把这一处与候选那处一起暴露。
     */
    confirmationId: row.confirmation_id == null ? '' : String(row.confirmation_id),
    candidateId: row.candidate_id == null ? '' : row.candidate_id,
    artifactId: row.artifact_id == null ? '' : row.artifact_id,
    artifactRevisionNo: row.artifact_revision_no == null ? '' : row.artifact_revision_no,
    carrier: String(row.carrier == null ? '' : row.carrier),
    vesselName: row.vessel_name ? String(row.vessel_name) : '',
    capacityText: String(row.capacity_tonnes == null ? '' : row.capacity_tonnes),
    loadBasis:
      (vessels > 1 ? vessels + ' 船承运' : '单船承运') + '·' + (partial ? '允许拆批' : '不拆批'),
    rateText:
      row.rate == null ? '' : String(row.rate) + (row.rate_unit ? ' 元/' + row.rate_unit : ''),
    currency: row.currency ? String(row.currency) : '',
    validUntil: row.valid_until ? String(row.valid_until) : '',
    evidenceKind: row.evidence_kind ? String(row.evidence_kind) : '',
    evidenceRef: row.evidence_ref ? String(row.evidence_ref) : '',
    demandText:
      row.demand_tonnes == null
        ? ''
        : String(row.demand_tonnes) + (row.demand_unit ? ' ' + row.demand_unit : ''),
    asOfDate: row.as_of_date ? String(row.as_of_date) : '',
    ruleSetVersion: row.rule_set_version ? String(row.rule_set_version) : '',
    /**
     * 这一次确认**覆盖的范围**。
     *
     * ⚠️ 它**不来自确认行** —— 确认表上没有这一列。事实来源是这次确认产出的
     *    **成果版本**：后端按冻结在确认行上的 `artifact_revision_id` 投影
     *    （`capacity._scope_from_payload`）⇒ 界面显示的东西与成果里写着的东西
     *    永远是同一份，不需要任何同步动作。
     * ⚠️ 空值时**不编一句话**：模板那边会明说「未记录」，而不是让它看起来像没这一项。
     */
    scopeText: row.agreed_scope ? String(row.agreed_scope) : '',
    note: row.note ? String(row.note) : '',
    confirmedAt: row.confirmed_at ? String(row.confirmed_at) : '',
    /**
     * 采购确认成果的**引用行文案**（整句在这儿拼，模板只渲染）。
     *
     * ⚠️ 与槽位引用同一条纪律（静态闸见 `scripts/verify_entrust_ui.js`：
     *    「引用行的文案只有一份」）：模板里拼「成果 #12 r1」会让"这条引用怎么写"
     *    散到两个地方 —— 改一处、漏一处，界面上就是两种写法，而且**不报错**。
     */
    artifactRefText:
      row.artifact_id == null
        ? ''
        : '采购确认成果 #' + row.artifact_id + ' r' + (row.artifact_revision_no == null ? '?' : row.artifact_revision_no),
    ruleRows: ruleRows,
    /**
     * 判定条数：**少一条就是"这条规则没跑"**。
     * 后端有 `_assert_every_rule_reported` 守这条（规则没跑全 ⇒ 程序性错误 ⇒ 500）；
     * 界面再报一次条数，是因为"跑了且通过"与"这次没跑"在数据上原本完全一样。
     */
    ruleCount: ruleRows.length,
    ruleSummary: ruleRows.length
      ? '逐规则判定 ' + passed + '/' + ruleRows.length + ' 通过'
      : '没有判定记录（不应发生，请报障）'
  }
}

/**
 * 只读复算结果的展示装饰（当前判定 ↔ 冻结判定并排）。
 *
 * ⚠️ `stillValid=false` **不等于**"这次确认被撤销了"：本端点**不写任何行**，
 * 正式的重做属 S4 的变更流程。界面必须把这句话说出来，否则
 * "900 吨候选在变更后不再适用"会被读成"确认失效了/被谁改掉了"。
 */
function decorateCapacityRecheck(r) {
  const row = r || {}
  const nowRows = capacityRuleRows({ rule_checks: row.rule_checks })
  const frozenRows = capacityRuleRows({ rule_checks: row.frozen_rule_checks })
  const changed = Array.isArray(row.changed_fields) ? row.changed_fields.map(String) : []
  const failed = nowRows.filter(function (x) {
    return !x.passed
  })
  return {
    confirmationId: row.confirmation_id == null ? '' : row.confirmation_id,
    stillValid: !!row.still_valid,
    validLabel: row.still_valid ? '按当前事实仍然成立' : '按当前事实已不再成立',
    asOfDate: row.as_of_date ? String(row.as_of_date) : '',
    candidateMissing: !!row.candidate_missing,
    changedFields: changed,
    changedText: changed.length
      ? changed.join('、')
      : '判定读到的列没有变化（结论变化来自别处，请报障）',
    nowRows: nowRows,
    frozenRows: frozenRows,
    /** 不通过的规则逐条说清（空串＝全部通过） */
    failedText: failed
      .map(function (x) {
        return x.ruleLabel + '：' + x.detail
      })
      .join('；'),
    note: row.still_valid
      ? '本次复算只读、未改任何行'
      : '本次复算只读、未改任何行；正式重做属变更流程，需要另一次确认'
  }
}

/** 候选运力清单（第 2 条"两家可比"要看的运力 / 价格口径 / 数量单位 / 有效期 / 证据）。 */
function fetchCapacityCandidates(assignmentId) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/capacity-candidates',
    method: 'GET'
  })
}

/** 该委托已作出的确认（响应体里**带逐规则判定** ⇒ 不必再逐条取详情）。 */
function fetchCapacityConfirmations(assignmentId) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/capacity-confirmations',
    method: 'GET'
  })
}

/** 单条确认详情：**冻结的判定输入** + 逐规则判定（不存在 ⇒ 404，不回空壳）。 */
function fetchCapacityConfirmation(confirmationId) {
  return request({ url: BASE + '/capacity-confirmations/' + confirmationId, method: 'GET' })
}

/** 只读复算：这条确认**现在还成立吗**（用当前事实重跑同一套规则，不改任何行）。 */
function recheckCapacityConfirmation(confirmationId) {
  return request({
    url: BASE + '/capacity-confirmations/' + confirmationId + '/recheck',
    method: 'GET'
  })
}

// ── 写命令 ──────────────────────────────────────────────────────────────────

/**
 * 登记一条候选运力事实（**不是确认**）。
 *
 * 后端 `record_candidate` 只拒"结构上不可能有意义"的输入（承运人空、吨位非正数、
 * 船数非正、单价与计价单位半边缺、证据类别不在取值域）；**"还没证据 / 还没有效期"
 * 是允许的** —— 否则 `expired ... cannot be confirmed` 那条规则永远触发不了
 * （过期数据根本进不来）。⇒ 界面**不替用户补默认值**，空就是空。
 */
function recordCapacityCandidate(assignmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/capacity-candidates',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 确认运力：跑规则闸门 ⇒ 通过才产出 `procurement_confirm` 成果 + 确认记录。
 *
 * `body` **只带** `candidate_id` 与 `agreed_scope`（+ 可选 `note`）：
 * 一切事实（承运人、吨位、船数、是否拆批、单价、有效期、证据）都由后端**从候选行读**。
 * 让请求体带这些值，会造出"确认的内容"与"候选运力"可以不一致的状态，而那条不一致
 * 在界面上看不出来 —— 于是"确认"退化成一次自述。
 *
 * 判定不通过 ⇒ **409**，响应体带**逐条**判定（含通过的），用 `capacityRuleRows` 渲染。
 */
function confirmCapacity(assignmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/capacity-confirmations',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

// ── 运输计划（三段航段）与必需任务前置（BP-03 第 1 条 / 合同 §10.1 第 4 步）──
//
// 一条取数：`GET /assignments/{aid}/plan` 回 `legs` + `task_prerequisites`。
//
// ⚠️ 服务端**不产出**"三段""公路—内河—公路"这类结论性文案 —— 段数是**行**的属性 ——
//    所以投影也**不拼**它：这里只把**每一段自己的**展示串拼好，段与段之间的关系
//    由模板按行渲染。拼成一句话就多一个会与数据脱节的落点。
//
// ⚠️ 与运力块**取向相反**：这一块**客户侧也取数、也显示**（后端那条读通道
//    `assert_can_view_assignment` 给货主本人放行）—— 航段是客户自己交进来的起讫路线，
//    而承运人 / 单价 / 缺口那些内部成本口径在运力那组。判据是**数据**，不是**身份**。

/** 该委托的三段计划 + 必需任务与固定前置。 */
function fetchAssignmentPlan(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId + '/plan', method: 'GET' })
}

/**
 * 计划投影：航段逐段成行 + 必需任务（含**固定前置**的可读指向）。
 *
 * 四条处置：
 * 1. `modeText` **只搬运**服务端的 `mode_label`（未登记取值时它等于原始 `mode` ——
 *    界面上出现一个陌生的英文标识，好过被硬塞进"公路"）；
 * 2. `routeText` 拼的是**这一段的**起终点，不是整条链 —— 整条链是行与行之间的关系；
 * 3. ⚠️ **前置解析不出来时不猜**：指向本单清单之外（数据异常）就退回
 *    `前置：任务 #<id>` —— id 是**事实**，标题是**猜的**。而 `precondition_task_id`
 *    为 `null` 是后端文档写明的**"没有前置"** ⇒ 只有那一种显示「无固定前置」。
 *    这两种必须长得不一样，否则"没有前置"与"前置指向谁不知道"会被读成同一件事；
 * 4. **两种空态分别说话**：`legs` 为空 ⇒「本单还没有结构化运输计划」；
 *    `tasks` 为空 ⇒「本单还没有派发任务」。两者的**处置完全不同**（前者要落方案、
 *    后者要派单），一律显示"暂无数据"会让人不知道该动哪一步。
 */
function decorateAssignmentPlan(payload) {
  const data = payload || {}
  const legs = (data.legs || []).map(function (leg) {
    const from = leg.from_name == null ? '' : String(leg.from_name)
    const to = leg.to_name == null ? '' : String(leg.to_name)
    return {
      legId: leg.leg_id == null ? '' : String(leg.leg_id),
      seqText: leg.seq == null ? '' : String(leg.seq),
      modeText: leg.mode_label == null ? '' : String(leg.mode_label),
      routeText: from + ' → ' + to,
      // 原始字段：**改段表单要回填**，而回填的必须是服务端的原值而不是显示文案。
      // 拿 `modeText`（标签，"公路"）去回填，提交时就会把"公路"当 `mode` 写回去 ——
      // 库里于是同时存在 `road` 与 `公路` 两种取值，而两种在界面上长得一模一样。
      modeRaw: leg.mode == null ? '' : String(leg.mode),
      fromRaw: from,
      toRaw: to
    }
  })
  const rawTasks = data.task_prerequisites || []
  // 截断事实（O-9）：服务端给 total 与 truncated；字段缺失时退回"行数即总数"，
  // 而不是默认"没被截" —— 后者会在老后端上把截断重新变回静默。
  const tasksTotal = data.task_prerequisites_total == null
    ? rawTasks.length
    : Number(data.task_prerequisites_total)
  const tasksTruncated = data.task_prerequisites_truncated === true
  // 前置**在本单清单内**解析：解析不到就不编标题（见上第 3 条）
  const titleById = {}
  rawTasks.forEach(function (t) {
    titleById[String(t.task_id)] = t.title == null ? '' : String(t.title)
  })
  const tasks = rawTasks.map(function (t) {
    const preId = t.precondition_task_id == null ? null : String(t.precondition_task_id)
    const preTitle = preId === null ? '' : titleById[preId] || ''
    return {
      taskIdText: t.task_id == null ? '' : String(t.task_id),
      titleText: t.title == null ? '' : String(t.title),
      statusText: TASK_STATUS_LABELS[t.status] || (t.status == null ? '' : String(t.status)),
      preText: preId === null ? '无固定前置' : '前置：' + (preTitle || '任务 #' + preId)
    }
  })
  return {
    assignmentId: data.assignment_id == null ? '' : String(data.assignment_id),
    legs: legs,
    tasks: tasks,
    hasLegs: legs.length > 0,
    hasTasks: tasks.length > 0,
    // ⚠️ 截断**必须**在界面上说出来（O-9）：任务被截时，某条任务的
    //    `precondition_task_id` 可能正好指向没读回来的那一行 ⇒ 它显示成
    //    「前置：任务 #7」（id 是事实、标题没有），与"这张单真的没有前置"长得一样。
    //    这一行就是两者的分界；`hasTasks` 为真时也可能截断，所以不能只在空态里说。
    tasksTruncated: tasksTruncated,
    tasksTruncatedText: tasksTruncated
      ? '只显示前 ' + tasks.length + ' 条（本单共 ' + tasksTotal + ' 条），未显示的任务也带前置关系'
      : ''
  }
}

// ── 航段命令（建段 / 改段留版本 / 版本历史）────────────────────────────────
//
// HO 2026-09-17 裁定的三条口径（转述，权威在 `backend/migrations/ent_leg_revision.py`）：
//   ① **参与方**都能建段 —— 不设角色门槛（货主与经理都行），但组织边界仍是硬界（非参与方 404）；
//   ② **不强制公–水–公** —— `mode` 不是枚举，未登记的取值原样显示；
//   ③ **改段留版本** —— 旧版不覆盖，每改一次追加一版快照。
//
// ⚠️ 界面上"能建段"这件事**不该**由前端再判一次：判权只在后端（非参与方拿 404）。
// 页面把按钮摆出来，是因为**能看到这张委托的人就是参与方** —— 与读模型的取向完全一致
// （见 `decorateAssignmentPlan` 的说明）。前端多写一份"我猜你能不能写"，只会多一处会过期的判断。

/**
 * 快捷填入的已登记运输方式。
 *
 * ⛔ 这是**输入助手**，不是取值域 —— 后端 `mode` 是自由字符串，本表外的值（如 `air`）
 * 照样能提交、并按原文显示。把这里当白名单校验，就等于用前端把"不强制 公–水–公"
 * 这条裁定推翻一半（`water` 之外的段会被拒），而那正是裁定要避免的事。
 */
const LEG_MODE_CHOICES = [
  { key: 'road', label: '公路' },
  { key: 'water', label: '内河' },
  { key: 'rail', label: '铁路' }
]

/** 版本历史的改动类型。未登记取值**原样显示**（同 `mode_label` 的口径）。 */
const LEG_CHANGE_KIND_LABELS = {
  created: '建段',
  updated: '改段'
}

/** 建一段航段（`POST /assignments/{aid}/legs`，幂等）。建段同时写下该段的第 1 版历史。 */
function createLeg(assignmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/legs',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 改一段航段（`PATCH /assignments/{aid}/legs/{leg_id}`，幂等）。
 *
 * `body` 只需带**要改的那些字段**：服务端以"值有没有变"为判据（传了但值相同 ⇒ 400
 * "这不是一次改动"），所以整份表单发过去也安全 —— 但这不等于前端可以不判：
 * **一条都没变**时页面应当在本地就把话说清（见 `buildLegBody`），少一次注定失败的往返。
 */
function updateLeg(assignmentId, legId, body, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/legs/' + legId,
    method: 'PATCH',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 某一段的版本历史（`GET /assignments/{aid}/legs/{leg_id}/revisions`）。 */
function fetchLegRevisions(assignmentId, legId) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/legs/' + legId + '/revisions',
    method: 'GET'
  })
}

/**
 * 版本历史投影：一版一行，每行说清**四件事** —— 第几版、是建还是改、当时是什么、谁/何时。
 *
 * 四条处置：
 * 1. `modeText` 只搬运服务端的 `mode_label`（同 `decorateAssignmentPlan`）；
 * 2. `routeText` 拼的是**这一版当时**的起终点 —— 这正是"留版本"要回答的问题，
 *    所以**不能**拿当前行去覆盖它（那就等于把历史抹平成现状）；
 * 3. `noteText` 空时写"未写改动说明"：`change_note` 为 `None` 是"当时没写"，
 *    不是"说明未知" —— 两种都不该显示成空白格；
 * 4. 时间**原样透出**（同 `_caseTime`）：不重排格式，是因为版本历史要靠时分秒排序 ——
 *    截成 `yyyy-MM-dd` 会让同一天的两版看起来一样，而那正是这条通道要回答的问题。
 */
function decorateLegRevisions(payload) {
  const rows = Array.isArray(payload) ? payload : []
  const items = rows.map(function (r) {
    const from = r.from_name == null ? '' : String(r.from_name)
    const to = r.to_name == null ? '' : String(r.to_name)
    const note = r.change_note == null ? '' : String(r.change_note)
    return {
      revisionIdText: r.revision_id == null ? '' : String(r.revision_id),
      revisionNoText: r.revision_no == null ? '' : '第 ' + String(r.revision_no) + ' 版',
      kindText:
        LEG_CHANGE_KIND_LABELS[r.change_kind] ||
        (r.change_kind == null ? '' : String(r.change_kind)),
      modeText: r.mode_label == null ? '' : String(r.mode_label),
      routeText: from + ' → ' + to,
      noteText: note || '未写改动说明',
      changedAtText: r.changed_at == null ? '' : String(r.changed_at)
    }
  })
  return {
    legId: rows.length ? String(rows[0].leg_id) : '',
    items: items,
    hasItems: items.length > 0
  }
}

// ── 合同派生与签署证据（§10.1 第 7 步 / BP-03 第 8 条 / D1-08）──────────────
//
// 两条命令 + 两条取数，**全部只有经理侧一条通道**：后端逐条走
// `assert_can_write_entrustment` / `assert_can_view_org`，不设货主旁路 ——
// 字段来源表里带 `release:12@v3` / `leg:4` 这类内部编号，那是审计信息。
// 客户要看合同走**已有的发布通路**（把 `contract_review` 发布出去、客户读冻结
// 快照），不为「客户看合同」新开一条通道，否则「客户能看到什么」会有两个判据。
//
// ⚠️ 派生**不带业务入参**：`note` 之外什么都不传。合同内容只能来自
//    「客户接受的那一版」，让界面能填金额就等于允许手编一份合同。
// ⚠️ 签署证据的 `mode` **不在这个模块里出现**：它恒为 `labeled_sample`、
//    由服务端写死，响应里的 `mode_text` 原样透出 —— 前端若再存一份标签表，
//    改一处另一处就静默显示旧说法（与航段 `mode_label` 同一条纪律）。

/**
 * 从该委托授权下的发布里挑出「客户已接受的对客报价」。
 *
 * 只看**已接受**的那一条：未响应 / 被拒绝都派生不出合同（服务端给 409），
 * 前端多判一次不是为了省那次请求 —— 摆一个必然失败的按钮，用户会以为是自己
 * 操作有误（与「有响应的发布不给撤回按钮」同一条理由）。
 */
function pickAcceptedQuoteRelease(items) {
  const rows = (items || []).filter(function (it) {
    const r = it || {}
    const resp = r.response || null
    return (
      String(r.artifact_type || '') === 'customer_quote' &&
      !!resp &&
      String(resp.decision || '') === 'accept'
    )
  })
  if (!rows.length) return null
  // 多条时取最新的那条：一份已接受事实只派生一份，但历史发布可能有多条。
  rows.sort(function (a, b) {
    return Number((b || {}).release_id || 0) - Number((a || {}).release_id || 0)
  })
  return rows[0]
}

/** 派生一份合同核对稿（`POST /offer-releases/{rid}/contract`，幂等）。 */
function deriveContract(releaseId, body, idempotencyKey) {
  return request({
    url: BASE + '/offer-releases/' + releaseId + '/contract',
    method: 'POST',
    data: body || {},
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * 读派生关系 + 逐字段来源（`GET /offer-releases/{rid}/contract`）。
 *
 * ⚠️ **还没派生过时是 404**，不是空壳：调用方必须把 404 与「读取失败」分开处置
 * （派生前那一步要显示入口，读失败那一步要说清原因）。
 */
function fetchContractDerivation(releaseId) {
  return request({ url: BASE + '/offer-releases/' + releaseId + '/contract', method: 'GET' })
}

/**
 * 记一条签署证据（`POST /contracts/{aid}/signature-evidence`，幂等）。
 *
 * `body` 只有 `evidence_kind` / `note` / `revision_no` —— **没有 `mode`**：
 * 模式由服务端写死，允许界面传就等于允许界面自称已完成电子签署。
 */
function recordSignatureEvidence(contractArtifactId, body, idempotencyKey) {
  return request({
    url: BASE + '/contracts/' + contractArtifactId + '/signature-evidence',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 该合同各版本的签署证据清单（`GET /contracts/{aid}/signature-evidence`）。 */
function fetchSignatureEvidence(contractArtifactId) {
  return request({
    url: BASE + '/contracts/' + contractArtifactId + '/signature-evidence',
    method: 'GET'
  })
}

/**
 * 派生结果投影：派生关系 + **逐字段来源表** + 如实列出的缺失项。
 *
 * 五条处置：
 * 1. 报价与合同的**精确版本**各自成串（D1-08 的那句 inspection）——
 *    只写「已派生」等于把「这份合同是从哪一版来的」留在库里没人回答；
 * 2. 来源行**同时给原值与文案**：界面显示「已接受报价」、比对用
 *    `accepted_release`；只给文案的话，两个取值其实不同这件事就看不出来了；
 * 3. 缺失项**列出来**而不是编默认值 —— 「这份合同少写了什么」必须看得见，
 *    编一个默认值就把「没写」说成「写了」；
 * 4. 备注为空写「未写备注」：`note` 为 null 是「当时没写」，不是「说明未知」；
 * 5. 生效日缺失写「未提供」，两种都不该显示成空白格。
 */
function decorateContractDerivation(payload) {
  const d = payload || {}
  const sources = (d.field_sources || []).map(function (s) {
    const one = s || {}
    const kind = one.source_kind == null ? '' : String(one.source_kind)
    const kindText = one.source_kind_text == null ? '' : String(one.source_kind_text)
    const ref = one.source_ref == null ? '' : String(one.source_ref)
    return {
      fieldPath: one.field_path == null ? '' : String(one.field_path),
      valueText: one.value_text == null ? '' : String(one.value_text),
      sourceKind: kind,
      sourceKindText: kindText || kind,
      sourceRef: ref,
      sourceText: (kindText || kind) + (ref ? ' · ' + ref : ''),
      // ⚠️ 模板的 `wx:key` **不能**用 `fieldPath`：同一个字段可以有**多行**来源
      // （"运输范围"一条条款由三个航段构成 ⇒ `route` 三行）。用非唯一键做 `wx:key`
      // 会让列表静默少渲染几行 —— 而"来源行数不对"正是 D1-08 要抓的那类问题。
      rowKey: [one.field_path, kind, ref].join('|')
    }
  })
  const absent = (d.absent_quote_fields || []).map(function (k) {
    return String(k == null ? '' : k)
  }).filter(function (k) {
    return !!k
  })
  const note = d.note == null ? '' : String(d.note)
  return {
    derivationIdText: '#' + _sid(d.derivation_id),
    releaseIdText: '#' + _sid(d.release_id),
    quoteArtifactId: _sid(d.quote_artifact_id),
    quoteRevisionNoText: '报价 v' + String(d.quote_revision_no == null ? '' : d.quote_revision_no),
    contractArtifactId: _sid(d.contract_artifact_id),
    contractRevisionNoText:
      '第 ' + String(d.contract_revision_no == null ? '' : d.contract_revision_no) + ' 版',
    templateText: String(d.template_code || '') + ' · ' + String(d.template_version || ''),
    effectiveDateText: d.effective_date ? String(d.effective_date) : '未提供',
    derivedAtText: d.derived_at == null ? '' : String(d.derived_at),
    noteText: note || '未写备注',
    absentText: absent.join('、'),
    hasAbsent: absent.length > 0,
    sourceCount: sources.length,
    sources: sources
  }
}

/**
 * 签署证据清单投影：一版一行 + 常驻声明 + 形态选项。
 *
 * 三条处置：
 * 1. 每行必须带**版本串**（「第 1 版」）—— 「有证据」与「证据签在哪个版本上」
 *    是两个问题，只答前一半等于把「客户签的是哪一版」留在库里没人回答；
 * 2. `disclaimer` 与 `kindOptions` **原样透出服务端**：前者是合同要求常驻的措辞，
 *    后者是取值域本身 —— 前端自己存一份就等于允许界面给出服务端不收的形态；
 * 3. 说明为空写「未写说明」，不留空白格。
 */
function decorateSignatureEvidence(payload) {
  const d = payload || {}
  const items = (d.items || []).map(function (it) {
    const one = it || {}
    return {
      evidenceIdText: '#' + _sid(one.evidence_id),
      revisionNoText: one.revision_no_text == null ? '' : String(one.revision_no_text),
      kind: one.evidence_kind == null ? '' : String(one.evidence_kind),
      kindText: one.evidence_kind_text == null ? '' : String(one.evidence_kind_text),
      modeText: one.mode_text == null ? '' : String(one.mode_text),
      noteText: one.note_text == null ? '' : String(one.note_text),
      recordedAtText: one.recorded_at == null ? '' : String(one.recorded_at)
    }
  })
  const kindOptions = (d.kind_options || []).map(function (o) {
    const one = o || {}
    return { key: String(one.value == null ? '' : one.value), label: String(one.label || '') }
  })
  return {
    contractArtifactId: _sid(d.contract_artifact_id),
    items: items,
    hasItems: !!d.has_items,
    disclaimer: d.disclaimer == null ? '' : String(d.disclaimer),
    kindOptions: kindOptions,
    hasKindOptions: kindOptions.length > 0
  }
}

// ── 委托货量变更（S6-1 / D1-09 / 合同 §10.1 第 8 步）────────────────────────
//
// 这一节只有**一个读函数 + 一个投影**，但它是 D1-09 那句
// `Apply the 800→950 change` 在界面上的落点：**在这张委托上**看到
// 「原来是 800、现在是 950、依据是什么、由哪个案件批的」。
//
// 写侧不在这里：货量变更是一条**经审批**的变更，走的是案件那三件已有的命令
// （登记受影响项 → 决定（带变更内容）→ 应用）。前端不新增"直接改货量"的命令 ——
// 那种入口会让"改货量"与"经审批"分离，而 D1-09 要的恰恰是后者。
//
// ⚠️ 为什么读的是**独立的**历史端点而不是委托详情里的 `quantity`：
// 详情里只有**当前值**。把 800 改成 950 之后，"原来是 800"这句话
// 在界面上就没有出处了 —— 而"从哪改到哪"正是这条判据要看的。

/** 该委托的货量变更历史（append-only，服务端已按应用时间升序）。 */
function fetchQuantityChanges(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId + '/quantity-changes' })
}

/**
 * 投影一行货量变更。
 *
 * `oldQuantityText` / `newQuantityText` / `changeText` 都用**服务端给的那份文案**
 * （`old_quantity_text` / `new_quantity_text`），前端不自己拼 `数值 + 单位`：
 * 各拼一份必然漂移，而漂移的表现是"历史里写的是 800 吨、详情里写的是 800.000吨"。
 *
 * 旧值**未知保持未知**（服务端给 `"未知"`）：从 NULL 改成确定值是合法变更，
 * 把它显示成 0 会让历史看起来像"从 0 涨到 950"。
 */
function decorateQuantityChange(row) {
  const d = row || {}
  const oldText = d.old_quantity_text || '未知'
  const newText = d.new_quantity_text || ''
  return {
    key: String(d.change_id == null ? '' : d.change_id),
    changeId: _sid(d.change_id),
    assignmentId: _sid(d.assignment_id),
    exceptionId: _sid(d.exception_id),
    baseRevision: d.base_revision === null || d.base_revision === undefined
      ? 0
      : Number(d.base_revision),
    // 原值（机器比对用）与文案（界面显示用）**都给**：界面显示文案、程序比对用原值。
    oldQuantity: d.old_quantity === null || d.old_quantity === undefined ? '' : String(d.old_quantity),
    newQuantity: d.new_quantity === null || d.new_quantity === undefined ? '' : String(d.new_quantity),
    oldQuantityText: oldText,
    newQuantityText: newText,
    // 一行就是那条对照（`800.000 吨 → 950.000 吨`）—— 判据要看的正是这一句，
    // 让它由**同一个函数**产出，页面模板就不必各自拼箭头。
    changeText: oldText + ' → ' + newText,
    basis: d.basis || '',
    basisText: d.basis || '未写依据',
    appliedBy: _caseUser(d.applied_by),
    appliedAt: _caseTime(d.applied_at),
    sourceText: '来源变更案件 #' + _sid(d.exception_id)
  }
}

/** 该委托的货量变更历史（空数组 = 从没改过，**不是**错误）。 */
function decorateQuantityChangeList(rows) {
  return (rows || []).map(decorateQuantityChange)
}

// ── 组装成果（人工定版；UI-06 / 计划 §5.2 S3）──────────────────────────────

/**
 * 手工创建成果（`POST /entrustments/{eid}/artifacts`，幂等）。
 *
 * 这是**「组装对客报价」的落点**，也是后端那条"把内容变成成果"的写入口之一
 * （另一条是采纳作业提案 `adoptJobProposal` —— 两者在后端共用一个服务函数，
 * 归属规则只有一份）。
 *
 * 为什么对客报价走**人工组装**而不是采纳 Agent 提案：
 *   1. 计划 §4 UI-06 与 §5.2 的 S3 DoD 都写着"对客报价**可人工组装**"；
 *   2. AG-02 自己的提案备注就写着"对客报价草稿 —— **定版发布前必须人工组装并确认口径**"；
 *   3. 发布前的**来源门槛**对两种来源判定不同（`offers.source_gate`）：人工直写的版本
 *      没有"待核验的模型声明"⇒ 无待核验项 ⇒ 可发布；而采纳模型提案时服务端会写下
 *      声明行 ⇒ 必须逐条核验后才放行。在核验入口尚未上线前，人工组装是唯一
 *      **不依赖未实现功能**的主演示通路。
 *
 * `body` 的字段按注册表契约（`GET /artifact-types` 的 `customer_quote`）：
 * 必填 `payload.amount` / `payload.currency` / `payload.includes`，
 * 可选 `payload.valid_until` / `payload.excludes` / `payload.note`。
 * `assignment_id` 一起传，成果才会落到**这一张**委托单上（不传就是"历史未归属成果"）。
 */
function createArtifact(entrustmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/entrustments/' + entrustmentId + '/artifacts',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

// ── 授权附件下载 ────────────────────────────────────────────────────────────

/**
 * 发布清单里某份附件的下载地址。
 *
 * 走的是 `/offer-releases/{id}/attachments/{aid}/download` —— **不是**通用附件下载口。
 * 通用口的判据是"对这条授权有可见性"，客户作为参与方对整条授权下的附件都有可见性
 * ⇒ 用通用口等于把内部底稿一起开给客户。这一条端点的判据**只有发布时冻结的那份清单**。
 */
function offerAttachmentDownloadUrl(releaseId, attachmentId) {
  return (
    BASE_URL +
    BASE +
    '/offer-releases/' +
    releaseId +
    '/attachments/' +
    attachmentId +
    '/download'
  )
}

/**
 * 下载发布清单里的附件并交给系统打开。
 *
 * 为什么不用 `wx.openDocument({filePath})` 直接打开本地文件：附件在服务端，
 * 必须先 `wx.downloadFile` 取到临时文件。而 `downloadFile` **不自动带鉴权头**
 * （与 `uploadFile` 同一类问题），漏了它会得到一个 401 的临时文件路径 ——
 * 表现为"下载成功但打开是乱码/空白"。
 *
 * 返回 Promise，并对 404 给出**可读**的失败原因：这一格的 404 有确定含义
 * （"这份附件不在客户可见清单里"），不是网络抖动。把它说成"下载失败，请重试"
 * 会让用户一直重试一件永远不可能成功的事。
 */
function downloadOfferAttachment(releaseId, attachmentId) {
  return new Promise(function (resolve, reject) {
    const header = {}
    const token = getToken()
    if (token) header.Authorization = 'Bearer ' + token
    wx.downloadFile({
      url: offerAttachmentDownloadUrl(releaseId, attachmentId),
      header: header,
      success(res) {
        if (res.statusCode !== 200) {
          const err = new Error('下载失败')
          err.httpStatus = res.statusCode
          reject(err)
          return
        }
        resolve({ filePath: res.tempFilePath, statusCode: res.statusCode })
      },
      fail(err) {
        const e = new Error((err && err.errMsg) || '下载失败')
        e.netError = true
        reject(e)
      }
    })
  })
}

// ── 投影 ────────────────────────────────────────────────────────────────────

/** 把客户冻结内容摊成可渲染的行（**只读**，标签来自成果字段表）。 */
function offerContentRows(content) {
  const c = content || {}
  return Object.keys(c).map(function (k) {
    const raw = c[k]
    const kind = _fieldKind(raw, '')
    return {
      name: k,
      label: artifactFieldLabel(k),
      value: kind === 'json' ? _structuredText(raw) : _scalarText(raw),
      structured: kind === 'json'
    }
  })
}

/** 客户视角的一条发布（`GET /my-offer-releases` / 按身份取的那条）。 */
function decorateCustomerOffer(row) {
  const d = row || {}
  const resp = d.response || null
  const origin = String(d.data_origin_mode || '') || 'unknown'
  return {
    releaseId: _sid(d.release_id),
    assignmentId: _sid(d.assignment_id),
    revisionNo: d.revision_no === null || d.revision_no === undefined ? 0 : Number(d.revision_no),
    status: d.status || '',
    statusLabel: offerStatusLabel(d.status),
    statusClass: offerStatusClass(d.status),
    releasedAt: d.released_at || '',
    typeCode: d.artifact_type || '',
    typeLabel: offerArtifactTypeLabel(d.artifact_type),
    contentRows: offerContentRows(d.content),
    contentEmpty: Object.keys(d.content || {}).length === 0,
    dataOriginMode: origin,
    dataOriginLabel: dataOriginLabel(origin),
    signatureMode: d.signature_mode || '',
    signatureLabel: signatureModeLabel(d.signature_mode),
    signatureHint: signatureModeHint(d.signature_mode),
    attachments: (d.authorized_attachment_ids || []).map(function (id) {
      return { attachmentId: _sid(id), label: '授权附件 #' + _sid(id) }
    }),
    canRespond: !!d.can_respond,
    decided: !!resp,
    decision: resp ? resp.decision || '' : '',
    decisionLabel: resp ? offerDecisionLabel(resp.decision) : '',
    responseNote: resp ? resp.note || '' : '',
    respondedAt: resp ? resp.responded_at || '' : ''
  }
}

/** 经理视角的一条发布：控制信息 + 客户快照 + 门槛 + 响应。 */
function decorateManagerRelease(row) {
  const d = row || {}
  const resp = d.response || null
  const gate = d.source_gate || {}
  const origin = d.data_origin || {}
  const pending = (gate.pending || []).length
  const rejected = (gate.rejected || []).length
  let gateHint = '来源已逐条核验'
  if (rejected) gateHint = '有 ' + rejected + ' 条来源被判定不可用'
  else if (pending) gateHint = '还有 ' + pending + ' 条来源待核验'
  return {
    releaseId: _sid(d.release_id),
    assignmentId: _sid(d.assignment_id),
    artifactId: _sid(d.artifact_id),
    revisionNo: d.revision_no === null || d.revision_no === undefined ? 0 : Number(d.revision_no),
    status: d.status || '',
    statusLabel: offerStatusLabel(d.status),
    statusClass: offerStatusClass(d.status),
    releasedAt: d.released_at || '',
    releasedBy: _sid(d.released_by),
    customerUserId: _sid(d.customer_user_id),
    closeReason: d.close_reason || '',
    dataOriginMode: String(origin.mode || 'unknown'),
    dataOriginLabel: dataOriginLabel(origin.mode),
    // 依据**只给经理**（客户投影没有这个字段）——"凭什么说这是 live"要有据可查
    dataOriginBasis: origin.basis ? JSON.stringify(origin.basis) : '',
    attachments: (d.authorized_attachment_ids || []).map(function (id) {
      return { attachmentId: _sid(id), label: '授权附件 #' + _sid(id) }
    }),
    gateOk: !!gate.ok,
    gatePending: pending,
    gateRejected: rejected,
    gateHint: gateHint,
    contentRows: offerContentRows((d.customer_snapshot || {}).payload || {}),
    decided: !!resp,
    decision: resp ? resp.decision || '' : '',
    decisionLabel: resp ? offerDecisionLabel(resp.decision) : '',
    responseNote: resp ? resp.note || '' : '',
    respondedAt: resp ? resp.responded_at || '' : '',
    // 有客户响应的发布**不能被撤回或取代**（后端会 409）⇒ 前端也不给这个按钮，
    // 免得用户点一个必然失败的按钮还以为是自己操作有误。
    canWithdraw: d.status === 'released' && !resp
  }
}

/** 客户侧：从"我收到的发布"里挑出**本单**的那条（多单混在一个列表里）。 */
function pickOfferForAssignment(items, assignmentId) {
  const want = String(assignmentId === null || assignmentId === undefined ? '' : assignmentId)
  if (!want) return null
  const mine = (items || []).filter(function (it) {
    return _sid(it && it.assignment_id) === want
  })
  if (!mine.length) return null
  // 取 release_id 最大的那条（同单可能有历史发布，最新的在最前）
  mine.sort(function (a, b) {
    return Number(b.release_id || 0) - Number(a.release_id || 0)
  })
  return mine[0]
}

/** 经理侧：某份成果的全部发布记录（成果页要按版本显示"这一版发过没有"）。 */
function offersForArtifact(items, artifactId) {
  const want = _sid(artifactId)
  return (items || [])
    .filter(function (it) {
      return _sid(it && it.artifact_id) === want
    })
    .sort(function (a, b) {
      return Number(b.release_id || 0) - Number(a.release_id || 0)
    })
}

/** 某份成果的某个版本是否已经发布过（成果页在版本行上给结论，不让用户猜）。
 *
 * ⚠️ **这里必须同时认 `revisionNo` 与 `revision_no`**，两个名字各有一段来历：
 * 传进来的通常是 `decorateManagerRelease` 的产出，那一层已经把名字改成
 * `revisionNo`；而导出出去之后，调用方也可能递来 API 原文（snake_case）。
 *
 * 只认一种的后果**不是报错，是整块静默失效**：`Number(undefined)` 是 `NaN`，
 * 而 `NaN === want` 恒为 `false` ⇒ 永远挑不到那条发布 ⇒ 成果页的版本行恒显示
 * 「未发布」、`releaseId` 恒为空串、**并且继续给**「发布这一版」的入口
 * （重复发布只会把客户手上那份取代掉，没有任何收益 —— 点下去就是一次误操作）。
 *
 * 2026-09-18 由 ㊹ 章**设备走查**抓到：静态门禁与 e2e 全绿，因为它们的夹具里
 * 没有「已发布」这一形态（`published=true` 的版本行从未被渲染出来过）。
 * 与 2149 行那类"读错键名就静默为 0"是同一种病。
 */
function releasedRevisionOf(releases, revisionNo) {
  const want = Number(revisionNo)
  const hit = (releases || []).filter(function (r) {
    const no = r && (r.revisionNo !== undefined ? r.revisionNo : r.revision_no)
    return Number(no) === want
  })[0]
  return hit || null
}

/** 某份成果的某个版本是否已经发布过 —— **按成果过滤后**再比版本号。
 *
 * ⚠️ 为什么必须有这一层（而不是让调用方自己 `filter`）：
 * 发布记录是按**授权**取的（`/entrustments/{eid}/offer-releases`），
 * **同一授权下可能有多个成果的发布**。而版本号只是成果**内部**的序号 ——
 * `v1` 在每份成果里都有。只比版本号会把"别的成果发过 v1"读成
 * "**本成果的 v1 发过了**" ⇒ 版本行显示"已发布"、**并且不给「发布这一版」入口**
 * ⇒ 用户再也发不出这一版，而页面上看不出为什么（**静默**）。
 *
 * 2026-09-18 由 ㊹ 章设备走查抓到；它是**第二个**缺陷 —— 第一个
 * （`releasedRevisionOf` 读错键名 ⇒ 恒不命中、`published` 恒 false）修好之后
 * 它才暴露出来：此前每次都是"恒未发布"，把这里的跨成果误判**掩盖**住了。
 * ⇒ 教训：修掉"恒假"之后，要回到**真实数据的形状**上再验一遍"恒真"的那一半。
 *
 * `releases` 可以是 `decorateManagerRelease` 的产出（驼峰）或 API 原文（下划线），
 * 两种形状都认。
 */
function releasedRevisionFor(releases, artifactId, revisionNo) {
  const wantArt = _sid(artifactId)
  const mine = (releases || []).filter(function (rel) {
    const id = rel && (rel.artifactId !== undefined ? rel.artifactId : rel.artifact_id)
    return _sid(id) === wantArt
  })
  return releasedRevisionOf(mine, revisionNo)
}

// ─────────────────────────────────────────────────────────────────────────────
// 财务与结算（S7-1 / S7-2 / S7-3 的界面入口 · §10.1 第 10–11 步）
// ─────────────────────────────────────────────────────────────────────────────
//
// 三片后端此前**都没有界面入口**：费用行的读与写、缺件清单与补录、结算版本与客户确认，
// 都只能在接口层演示 —— 而裁定 Q4=A 明确要求第 10–12 步**经现有产品界面演示**。
//
// 三条通道的可见性**不同**，本层不做二次判断，只把服务端的结论照实投影：
//   · 费用与结算的**内部**读数（版本链 / 财务状态）**没有货主面** —— 行上带对手方与内部成本，
//     页面按本地组织权限判一次再取数（与运力那一组同口径）；
//   · **对客投影**（`customer-view`）走委托授权链，**货主本人可见**；
//   · **客户确认**只有货主本人能做，经理侧调用会拿到 403（界面据此给出可读文案）。

/** 该委托的费用行与合计（经理视角；合计按「币种 × 收付方向」分开）。 */
function fetchCharges(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId + '/charges' })
}

/** 登记一条费用行（登记后是 `draft`，**草稿不进合计**）。 */
function recordCharge(assignmentId, body, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/charges',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 确认费用行（`draft → confirmed`；确认后才进合计）。 */
function confirmCharge(chargeId, body, idempotencyKey) {
  return request({
    url: BASE + '/charges/' + chargeId + '/confirm',
    method: 'POST',
    data: body || {},
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 对已确认的费用提争议（`confirmed → disputed`；**争议行不进合计**）。 */
function disputeCharge(chargeId, body, idempotencyKey) {
  return request({
    url: BASE + '/charges/' + chargeId + '/dispute',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 处置争议：**必须显式给出** `counts_in_total`（裁定 Q2=B，不由状态推导）。 */
function resolveCharge(chargeId, body, idempotencyKey) {
  return request({
    url: BASE + '/charges/' + chargeId + '/resolve',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 缺什么证据（派生：`required_evidence` − 已登记类别；含交接类任务的汇总）。 */
function fetchEvidenceGaps(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId + '/evidence-gaps' })
}

/** 在**原任务**上补录一条证据（缺件的补救入口；`occurred_at` 是**业务发生时间**）。 */
function recordTaskEvidence(taskId, body, idempotencyKey) {
  return request({
    url: BASE + '/tasks/' + taskId + '/evidence',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 按当前费用事实生成一个**新**结算版本（旧版本一字不改）。 */
function createSettlement(assignmentId, idempotencyKey) {
  return request({
    url: BASE + '/assignments/' + assignmentId + '/settlements',
    method: 'POST',
    data: {},
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 结算版本链（升序）＋ 适用版本 id（＝最大版本号那一行）。 */
function fetchSettlements(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId + '/settlements' })
}

/** 内部确认（`draft → approved`；只能确认适用版本）。 */
function approveSettlement(settlementId, body, idempotencyKey) {
  return request({
    url: BASE + '/settlements/' + settlementId + '/approve',
    method: 'POST',
    data: body || {},
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/**
 * **客户确认该精确版本**（只有货主本人能做；一版只确认一次）。
 *
 * 经理侧调用会拿到 403 —— 那句文案是服务端给的，界面**不自己编**
 * （"客户确认只能由客户本人做"这条口径的正文在后端）。
 */
function confirmSettlement(settlementId, body, idempotencyKey) {
  return request({
    url: BASE + '/settlements/' + settlementId + '/customer-confirm',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** 记一条收付依据（只能挂在已确认的版本上；`mode` 不由客户端给）。 */
function recordSettlementPayment(settlementId, body, idempotencyKey) {
  return request({
    url: BASE + '/settlements/' + settlementId + '/payments',
    method: 'POST',
    data: body,
    headers: { 'Idempotency-Key': idempotencyKey }
  })
}

/** **对客投影**：只出对客（应收）费用与白名单字段，不暴露内部供应商成本。 */
function fetchCustomerSettlement(settlementId) {
  return request({ url: BASE + '/settlements/' + settlementId + '/customer-view' })
}

/** 财务结案状态（派生；`blockers` 逐条给出未结成因）。 */
function fetchFinancialStatus(assignmentId) {
  return request({ url: BASE + '/assignments/' + assignmentId + '/financial-status' })
}

//: 费用状态 → 界面文案。**未登记的取值原样显示**（未知不假装知道）。
const CHARGE_STATUS_LABELS = {
  draft: '草稿',
  confirmed: '已确认',
  disputed: '有争议',
  resolved: '已解决',
  rejected: '已拒绝'
}

//: 处置结果 → 文案。
const CHARGE_OUTCOME_LABELS = {
  accepted: '认可',
  adjusted: '调减',
  rejected: '拒绝'
}

//: 结算版本状态 → 文案。
const SETTLEMENT_STATUS_LABELS = { draft: '待内部确认', approved: '已内部确认' }

//: 客户决定 → 文案。`accepted` / `rejected` 之外一律"未确认"
//: （**未知保持未知**：把未登记的决定显示成"已接受"会让结案依据凭空成立）。
const SETTLEMENT_DECISION_LABELS = { accepted: '客户已接受', rejected: '客户不接受' }

//: 财务状态 → 文案。
const FINANCIAL_STATUS_LABELS = {
  not_started: '未开始',
  open: '未结清',
  settled: '已结清'
}

//: 未结成因 → 界面短文案（`blockers[].code`）。**未知码原样显示**，不吞。
const FINANCIAL_BLOCKER_LABELS = {
  unsettled_charges: '有费用行还没有结论',
  settlement_missing: '还没有结算版本',
  settlement_not_approved: '适用结算版本尚未内部确认',
  customer_not_confirmed: '客户尚未确认适用结算版本',
  settlement_stale: '适用结算版本已过期（费用事实变了）',
  open_cases: '有未关闭的案件',
  balance_unsettled: '未结余额非零'
}

/**
 * 投影一条费用行。
 *
 * ⭐ **金额一律用服务端给的字符串**（不经 `Number`）：跨语言消费者不会因 IEEE754
 * 丢精度，而界面这一侧"显示 12000 还是 12000.0000"必须与服务端同一个口径 ——
 * 前端自己 `toFixed` 就是第二套口径，迟早与合计对不上。
 */
function decorateCharge(row) {
  const d = row || {}
  return {
    key: String(d.charge_id == null ? '' : d.charge_id),
    chargeId: _sid(d.charge_id),
    direction: d.direction || '',
    directionText: d.direction === 'receivable' ? '应收' : d.direction === 'payable' ? '应付' : (d.direction || '未知'),
    chargeKind: d.charge_kind || '',
    quantityText: d.quantity ? String(d.quantity) + (d.unit ? ' ' + d.unit : '') : '',
    amountText: d.amount === null || d.amount === undefined ? '未知' : String(d.amount),
    currency: d.currency || '',
    counterparty: d.counterparty || '',
    basis: d.basis || '',
    status: d.status || '',
    statusText: CHARGE_STATUS_LABELS[d.status] || d.status || '未知',
    // ⚠️ `counts_in_total` **未知是 `null`，不是 `false`** —— 未处置的行本来就没有这个结论
    countsInTotal: d.counts_in_total === true,
    countsKnown: d.counts_in_total === true || d.counts_in_total === false,
    countsText: d.counts_in_total === true ? '计入合计' : d.counts_in_total === false ? '不计入合计' : '计入未定',
    disputedReason: d.disputed_reason || '',
    outcomeText: CHARGE_OUTCOME_LABELS[d.resolution_outcome] || d.resolution_outcome || '',
    resolutionAmountText: d.resolution_amount ? String(d.resolution_amount) : '',
    resolutionMethod: d.resolution_method || '',
    revision: Number(d.revision || 0),
    canConfirm: d.status === 'draft',
    canDispute: d.status === 'confirmed',
    canResolve: d.status === 'disputed'
  }
}

/** 投影一个合计分组（按「币种 × 收付方向」分开，**不跨币种相加**）。 */
function decorateChargeTotal(row) {
  const d = row || {}
  return {
    key: String(d.currency || '') + '#' + String(d.direction || ''),
    currency: d.currency || '',
    direction: d.direction || '',
    directionText: d.direction === 'receivable' ? '应收' : d.direction === 'payable' ? '应付' : (d.direction || '未知'),
    totalText: d.total === null || d.total === undefined ? '未知' : String(d.total),
    countedLines: Number(d.counted_lines || 0),
    excludedLines: Number(d.excluded_lines || 0)
  }
}

/** 投影任务的一个证据缺项。 */
function decorateEvidenceGap(row) {
  const d = row || {}
  return {
    key: String(d.task_id == null ? '' : d.task_id),
    taskId: _sid(d.task_id),
    taskType: d.task_type || '',
    taskTypeText: TASK_TYPE_LABELS[d.task_type] || d.task_type || '未知',
    title: d.title || '',
    status: d.status || '',
    isHandover: d.is_handover === true,
    required: (d.required || []).slice(),
    registered: (d.registered || []).slice(),
    missing: (d.missing || []).slice(),
    missingText: (d.missing || []).join('、'),
    registeredText: (d.registered || []).length ? (d.registered || []).join('、') : '（暂无）',
    satisfied: d.satisfied === true,
    waitReason: d.wait_reason || '',
    waitingOnEvidence: d.waiting_on_evidence === true,
    canRecord: (d.missing || []).length > 0
  }
}

/** 投影一个结算版本。 */
function decorateSettlement(row) {
  const d = row || {}
  const lines = (d.lines || []).map(function (line) {
    const l = line || {}
    return {
      key: String(l.charge_id == null ? '' : l.charge_id),
      directionText: l.direction === 'receivable' ? '应收' : l.direction === 'payable' ? '应付' : (l.direction || '未知'),
      chargeKind: l.charge_kind || '',
      amountText: l.amount === null || l.amount === undefined ? '未知' : String(l.amount),
      currency: l.currency || '',
      basis: l.basis || ''
    }
  })
  return {
    key: String(d.settlement_id == null ? '' : d.settlement_id),
    settlementId: _sid(d.settlement_id),
    versionNo: Number(d.version_no || 0),
    versionText: 'v' + String(d.version_no == null ? '' : d.version_no),
    status: d.status || '',
    statusText: SETTLEMENT_STATUS_LABELS[d.status] || d.status || '未知',
    currency: d.currency || '',
    customerTotalText: d.customer_total === null || d.customer_total === undefined ? '未知' : String(d.customer_total),
    internalTotalText: d.internal_total === null || d.internal_total === undefined ? '未知' : String(d.internal_total),
    lineCount: Number(d.line_count || 0),
    lines: lines,
    approvedAt: d.approved_at || '',
    customerConfirmedAt: d.customer_confirmed_at || '',
    customerDecision: d.customer_decision || '',
    decisionText: SETTLEMENT_DECISION_LABELS[d.customer_decision] || '客户未确认',
    customerNote: d.customer_note || '',
    canApprove: d.status === 'draft',
    // 客户确认要求：已内部确认，且这一版还没有客户决定
    canConfirm: d.status === 'approved' && !d.customer_confirmed_at,
    revision: Number(d.revision || 0)
  }
}

module.exports = {
  BASE,
  SAMPLE_QUOTE_FILENAME,
  SAMPLE_QUOTE_TEXT,
  ARTIFACT_FIELD_KINDS,
  ARTIFACT_FIELD_LABELS,
  ARTIFACT_STATUS_LABELS,
  CASE_CLOSURE_DISPOSITIONS,
  CASE_DECIDE_EXCLUDED,
  CASE_DISPOSITION_LABELS,
  CASE_DISPOSITIONS_WITHOUT_APPLICATION,
  CASE_ELEMENTS,
  CASE_EVENT_LABELS,
  CASE_IMPACT_CLASS,
  CASE_IMPACT_LABELS,
  CASE_IMPACT_ORDER,
  CASE_KIND_LABELS,
  CASE_KIND_ORDER,
  CASE_ORG_SCOPE_LABELS,
  CASE_ORG_SCOPE_ORDER,
  CASE_SEVERITY_LABELS,
  CASE_SEVERITY_ORDER,
  CASE_SOURCE_LABELS,
  CASE_STATUS_CLASS,
  CASE_STATUS_CLOSED,
  CASE_STATUS_LABELS,
  CASE_TARGET_LABELS,
  CASE_TRANSITIONS,
  CHANGE_CATEGORY_LABELS,
  EXTRACT_STATUS_LABELS,
  EXTRACT_STATUS_ORDER,
  ISSUE_KIND_LABELS,
  JOB_STATUS_CLASS,
  JOB_STATUS_LABELS,
  LEG_CHANGE_KIND_LABELS,
  LEG_MODE_CHOICES,
  MESSAGE_ROLE_LABELS,
  MESSAGE_SOURCE_LABELS,
  ORG_PERM_CLAIM,
  ORG_PERM_QUOTE_CREATE,
  ORG_PERM_VIEW,
  ORG_PERMISSION_LABELS,
  ORG_ROLE_LABELS,
  REF_PROJECTORS,
  REVALIDATION_STATUS_CLASS,
  REVALIDATION_STATUS_LABELS,
  REVISION_ROLE,
  REVISION_SOURCE_LABELS,
  SESSION_STATUS_LABELS,
  SLOT_EMPTY_TEXT,
  SLOT_FIELD_LABELS,
  SOURCE_KIND_LABELS,
  SOURCE_KIND_ORDER,
  STATUS_HINT,
  STATUS_META,
  STATUS_ORDER,
  STRUCTURED_FIELD_KINDS,
  TASK_STATUS_LABELS,
  TASK_TYPE_LABELS,
  TASK_TYPE_ORDER,
  TEXT_SOURCE_LABELS,
  TEXT_SOURCE_ORDER,
  VIEW,
  WORKBENCH_SLOTS,
  addCaseLink,
  adoptJobProposal,
  applyCase,
  appendMessage,
  appendRevision,
  assignmentDraftBody,
  assignmentWriteError,
  artifactFieldLabel,
  artifactStatusClass,
  artifactStatusLabel,
  buildCapacityComparison,
  buildPayload,
  canClaimAssignment,
  candidateStatusLabel,
  capacityEvidenceLabel,
  capacityOutcomeLabel,
  capacityRuleLabel,
  capacityRuleRows,
  caseClosureOptions,
  caseCategoryOptions,
  caseCreateBody,
  caseDecideAvailable,
  caseDecisionOptions,
  caseImpactOptions,
  caseKindOptions,
  caseOptionList,
  caseOrgListQuery,
  caseSeverityOptions,
  caseWriteError,
  claimAssignment,
  closeCase,
  coerceLike,
  confirmArtifact,
  confirmCapacity,
  confirmCard,
  createAssignment,
  createCase,
  createLeg,
  createSession,
  createTask,
  createArtifact,
  decideCase,
  deriveContract,
  decorateAssignment,
  decorateAssignmentPlan,
  decorateAttachment,
  decorateArtifact,
  decorateLegRevisions,
  decorateCase,
  decorateCaseLinkTargets,
  decorateCaseList,
  decorateCaseRow,
  decorateCapacityCandidate,
  decorateCapacityConfirmation,
  decorateCapacityRecheck,
  decorateContractDerivation,
  decorateQuantityChange,
  decorateQuantityChangeList,
  decorateDetail,
  decorateEntrustment,
  decorateEntrustments,
  decorateJob,
  decorateList,
  decorateMessage,
  decorateOrg,
  decorateOrgs,
  decorateRevisions,
  decorateSession,
  decorateSignatureEvidence,
  decorateSlot,
  decorateWorkbench,
  entryDecision,
  extractAttachment,
  extractStatusLabel,
  fetchArtifact,
  fetchArtifactCandidates,
  fetchArtifactTypes,
  fetchAssignment,
  fetchAssignmentPlan,
  fetchCapacityCandidates,
  fetchCapacityConfirmation,
  fetchCapacityConfirmations,
  fetchCase,
  fetchCaseOrgList,
  fetchContractDerivation,
  fetchQuantityChanges,
  fetchEntrustmentAttachments,
  fetchJob,
  fetchJobs,
  fetchLegRevisions,
  fetchMine,
  fetchMyEntrustments,
  fetchMyOrgs,
  fetchQueue,
  fetchRevisions,
  fetchSession,
  fetchSignatureEvidence,
  fetchSessionContext,
  fetchSessions,
  fetchTaskCandidates,
  fetchWorkbench,
  fieldDrafts,
  fieldKindHint,
  isArtifactDirty,
  isDispositionWithoutApplication,
  jobFailureText,
  jobStatusClass,
  jobStatusLabel,
  messageRoleLabel,
  messageSourceLabel,
  newIdempotencyKey,
  pageHint,
  permittedOrgIds,
  // 单组织查询（`permittedOrgIds` 的配套）。此前只在模块内部被 `canClaimAssignment`
  // 用着、没导出 —— 页面因此只能自己写 `permitted[String(orgId)]`，那就是**第二份判据**：
  // 归一规则（`org_id` 为空 / 数字与字符串同型）一旦在一侧漂移，表现是"按钮时而出现
  // 时而不出现"，两边单看都对。
  isPermittedOrg,
  pickAcceptedQuoteRelease,
  pickEntrustment,
  pickOrg,
  probeEntry,
  probeOwnerEntry,
  recheckCapacityConfirmation,
  recordCapacityCandidate,
  recordSignatureEvidence,
  removeCaseLink,
  reopenCase,
  revisionSourceLabel,
  runJob,
  sessionStatusLabel,
  sourceKindLabel,
  statusClass,
  statusLabel,
  submitAssignment,
  submitJob,
  textSourceLabel,
  transcribeAttachment,
  updateLeg,
  uploadAttachment,
  viewState,
  CANDIDATE_STATUS_LABELS,
  CANDIDATE_STATUS_ORDER,
  CAPACITY_EVIDENCE_LABELS,
  CAPACITY_EVIDENCE_ORDER,
  CAPACITY_OUTCOME_LABELS,
  CAPACITY_RULE_LABELS,
  DATA_ORIGIN_LABELS,
  DATA_ORIGIN_ORDER,
  OFFER_ARTIFACT_TYPE_LABELS,
  OFFER_DECISION_LABELS,
  OFFER_STATUS_META,
  OFFER_STATUS_ORDER,
  SIGNATURE_MODE_LABELS,
  dataOriginLabel,
  decorateCustomerOffer,
  decorateManagerRelease,
  downloadOfferAttachment,
  fetchEntrustmentOfferReleases,
  fetchMyOfferReleases,
  fetchOfferRelease,
  offerArtifactTypeLabel,
  offerAttachmentDownloadUrl,
  offerContentRows,
  offerDecisionLabel,
  offerStatusClass,
  offerStatusLabel,
  offersForArtifact,
  pickOfferForAssignment,
  releaseOffer,
  releasedRevisionFor,
  releasedRevisionOf,
  respondOffer,
  signatureModeHint,
  signatureModeLabel,
  withdrawOffer,
  // ── 财务与结算（§10.1 第 10–11 步的界面入口）──────────────────────────
  CHARGE_OUTCOME_LABELS,
  CHARGE_STATUS_LABELS,
  FINANCIAL_BLOCKER_LABELS,
  FINANCIAL_STATUS_LABELS,
  SETTLEMENT_DECISION_LABELS,
  SETTLEMENT_STATUS_LABELS,
  approveSettlement,
  confirmCharge,
  confirmSettlement,
  createSettlement,
  decorateCharge,
  decorateChargeTotal,
  decorateEvidenceGap,
  decorateSettlement,
  disputeCharge,
  fetchCharges,
  fetchCustomerSettlement,
  fetchEvidenceGaps,
  fetchFinancialStatus,
  fetchSettlements,
  recordCharge,
  recordSettlementPayment,
  recordTaskEvidence,
  resolveCharge
}
