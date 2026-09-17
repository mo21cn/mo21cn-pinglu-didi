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

const { request } = require('./request')

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
const CASE_TARGET_LABELS = { task: '任务', artifact: '成果' }

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
                  : '版本 id ' + t.basis_revision_id
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
 *   · 任务 → `GET /entrust/tasks?assignment_id=`；
 *   · 成果 → `GET /entrust/assignments/{id}/artifacts`。
 * 两者都是**该委托的可见性**，与案件一致；不需要额外授权。
 */
function fetchTaskCandidates(assignmentId, size) {
  return request({
    url: BASE + '/tasks',
    method: 'GET',
    data: { assignment_id: assignmentId, page: 1, size: size || 50 }
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
function decorateCaseLinkTargets(tasks, artifacts, types) {
  const specs = types || []
  const typeLabel = function (code) {
    for (let i = 0; i < specs.length; i++) {
      if (specs[i] && specs[i].code === code) return specs[i].label || code
    }
    return code
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
  return taskRows.concat(artRows)
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

function decorateAttachment(row) {
  const data = row || {}
  return {
    attachmentId: _sid(data.attachment_id),
    name: data.name || data.filename || '未命名附件',
    extractStatus: data.extract_status || '',
    extractLabel:
      data.extract_status === 'done'
        ? '已提取文本'
        : data.extract_status
          ? '未提取'
          : '状态未知'
  }
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

module.exports = {
  BASE,
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
  ISSUE_KIND_LABELS,
  JOB_STATUS_CLASS,
  JOB_STATUS_LABELS,
  MESSAGE_ROLE_LABELS,
  MESSAGE_SOURCE_LABELS,
  ORG_PERM_CLAIM,
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
  STATUS_HINT,
  STATUS_META,
  STATUS_ORDER,
  STRUCTURED_FIELD_KINDS,
  TASK_STATUS_LABELS,
  TASK_TYPE_LABELS,
  TASK_TYPE_ORDER,
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
  buildPayload,
  canClaimAssignment,
  caseClosureOptions,
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
  confirmCard,
  createAssignment,
  createCase,
  createSession,
  createTask,
  decideCase,
  decorateAssignment,
  decorateAttachment,
  decorateArtifact,
  decorateCase,
  decorateCaseLinkTargets,
  decorateCaseList,
  decorateCaseRow,
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
  decorateSlot,
  decorateWorkbench,
  entryDecision,
  fetchArtifact,
  fetchArtifactCandidates,
  fetchArtifactTypes,
  fetchAssignment,
  fetchCase,
  fetchCaseOrgList,
  fetchEntrustmentAttachments,
  fetchJob,
  fetchJobs,
  fetchMine,
  fetchMyEntrustments,
  fetchMyOrgs,
  fetchQueue,
  fetchRevisions,
  fetchSession,
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
  pickEntrustment,
  pickOrg,
  probeEntry,
  probeOwnerEntry,
  removeCaseLink,
  reopenCase,
  revisionSourceLabel,
  runJob,
  sessionStatusLabel,
  statusClass,
  statusLabel,
  submitAssignment,
  submitJob,
  viewState
}
