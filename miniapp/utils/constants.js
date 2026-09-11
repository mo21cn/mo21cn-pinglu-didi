// 业务常量唯一来源（第三方审计 P3-4 收敛：原 8 个页面各自复制，存在 2 处口径漂移）
//
// 口径说明（统一规则）：
//   - tanker 的展示名统一为「液货船」（原 berth.js 写成「油船」，与其余 7 处不一致 → 收敛）
//   - SHIP_TYPES    = 4 个真实船型（备案/展示用）
//   - SHIP_TYPES_ANY= 带「按货物条件匹配」空项的 5 项（货主发布货源的筛选用，原 cargo/shipper 两页的形态）
//   - 港口的「全量名 / 短名 / 选择器」在 utils/ports.js（PORTS + portLabel），不在此重复

const CARGO_TYPES = [
  { key: 'bulk',      label: '散货' },
  { key: 'general',   label: '件杂货' },
  { key: 'container', label: '集装箱' },
  { key: 'tanker',    label: '液货' },
  { key: 'other',     label: '其他' }
]

const PACKS = ['散装', '袋装', '托盘', '裸装', '罐装']

const SHIP_TYPES = [
  { key: 'bulk',      label: '散货船' },
  { key: 'general',   label: '件杂货船' },
  { key: 'container', label: '集装箱船' },
  { key: 'tanker',    label: '液货船' }
]

/** 货主侧「指定船型」筛选用（首项空 = 不限制，按货物条件匹配） */
const SHIP_TYPES_ANY = [{ key: '', label: '按货物条件匹配' }].concat(SHIP_TYPES)

const SHIP_TYPE_LABELS = {
  bulk: '散货船',
  general: '件杂货船',
  container: '集装箱船',
  tanker: '液货船'
}

/** 港代码 → 短展示名（订单卡/泊位卡等紧凑场景；选择器场景用 utils/ports.js 的全量名） */
const PORT_LABELS = {
  NNG: '南宁', GGU: '贵港', WUZ: '梧州', BIN: '来宾', LZH: '柳州',
  BSZ: '百色', CHZ: '崇左', GXL: '桂林', HEZ: '贺州', YUL: '玉林',
  QNZ: '钦州', FCG: '防城港', BHZ: '北海'
}

module.exports = { CARGO_TYPES, PACKS, SHIP_TYPES, SHIP_TYPES_ANY, SHIP_TYPE_LABELS, PORT_LABELS }
