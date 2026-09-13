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
    return { state: VIEW.EMPTY, title: '还没有委托', hint: '货主提交委托后会出现在这里' }
  }
  return { state: VIEW.READY, title: '', hint: '' }
}

/** 把接口载荷整理成模板直接可用的形状（模板不做事，避免 wxml 里写表达式）。 */
function decorateAssignment(row) {
  const data = row || {}
  const quantity = data.quantity === null || data.quantity === undefined ? '' : String(data.quantity)
  const unit = data.quantity_unit || ''
  return {
    assignmentId: data.assignment_id,
    title: data.title || '未命名委托',
    cargoSummary: data.cargo_summary || '未填写货类',
    quantityText: quantity ? quantity + (unit ? ' ' + unit : '') : '货量未填写',
    status: data.status,
    statusLabel: statusLabel(data.status),
    statusClass: statusClass(data.status),
    revision: data.revision,
    createdAt: data.created_at || '',
    orgId: data.org_id === null || data.org_id === undefined ? '' : String(data.org_id)
  }
}

function decorateList(rows) {
  return (rows || []).map(decorateAssignment)
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

/** 拉取组织委托队列（经理工作台的数据源）。 */
function fetchQueue(options) {
  const opts = options || {}
  const data = { view: 'org', page: opts.page || 1, size: opts.size || 20 }
  if (opts.orgId) data.org_id = opts.orgId
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
 */
function decorateDetail(row) {
  const data = row || {}
  const decorated = decorateAssignment(data)
  decorated.cargoSummary = data.cargo_summary || '未填写货类'
  decorated.quantityText = decorated.quantityText
  decorated.statusHint = STATUS_HINT[data.status] || ''
  decorated.createdAt = data.created_at || ''
  decorated.orgText = decorated.orgId ? '组织 #' + decorated.orgId : '未指定组织'
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
 */
const WORKBENCH_SLOTS = [
  { key: 'overview', title: '委托概况', taskType: '' },
  { key: 'plan_tasks', title: '方案与任务', taskType: '', taskPick: true },
  { key: 'procurement', title: '采购与报价', taskType: 'purchase' },
  { key: 'customer_contracts', title: '对客方案与合同', taskType: 'contract' },
  { key: 'execution', title: '履约与交接', taskType: 'execution' },
  { key: 'exceptions', title: '异常与变更', taskType: '' },
  { key: 'settlement', title: '费用与结案', taskType: 'settlement' }
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

/** 成果精确版本引用（PRD 第 187 行：对话与工作台引用**同一** artifact ID 与版本） */
function _artifactRefs(slot) {
  const current = slot.current || {}
  return (current.refs || []).map(function (ref) {
    const version = ref.revision_no === null || ref.revision_no === undefined ? '—' : ref.revision_no
    return {
      artifactId: ref.artifact_id,
      label: ref.label || ref.artifact_type,
      revisionNo: ref.revision_no,
      text: (ref.label || ref.artifact_type) + ' · v' + version
    }
  })
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
    refs: _artifactRefs(slot),
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

module.exports = {
  BASE,
  ISSUE_KIND_LABELS,
  ORG_PERMISSION_LABELS,
  ORG_ROLE_LABELS,
  SLOT_EMPTY_TEXT,
  SLOT_FIELD_LABELS,
  STATUS_HINT,
  STATUS_META,
  STATUS_ORDER,
  TASK_TYPE_LABELS,
  TASK_TYPE_ORDER,
  VIEW,
  WORKBENCH_SLOTS,
  claimAssignment,
  createTask,
  decorateAssignment,
  decorateDetail,
  decorateList,
  decorateOrg,
  decorateOrgs,
  decorateSlot,
  decorateWorkbench,
  entryDecision,
  fetchAssignment,
  fetchMyOrgs,
  fetchQueue,
  fetchWorkbench,
  newIdempotencyKey,
  pageHint,
  pickOrg,
  probeEntry,
  statusClass,
  statusLabel,
  viewState
}
