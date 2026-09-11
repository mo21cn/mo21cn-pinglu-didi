// 平陆运河沿线港口（13 个）· 全站唯一来源
//
// ⚠️ 为什么不用 wx.showActionSheet 选港口：
//   该 API 的 itemList 上限为 6 项，超过会 fail（errMsg: itemList should not be larger than 6），
//   而港口有 13 个 —— 原先 5 个页面各自用 showActionSheet(PORTS.map(...)) 选择港口，
//   点击后弹层根本不出现、又没有 fail 兜底，表现为「点了没反应 / 没有下拉选择」。
//   → 港口选择统一走自定义组件 components/port-picker（滚动列表，可承载任意长度）。

const PORTS = [
  { key: 'NNG', label: '南宁 · 平塘港' },
  { key: 'GGU', label: '贵港' },
  { key: 'WUZ', label: '梧州' },
  { key: 'BIN', label: '来宾' },
  { key: 'LZH', label: '柳州' },
  { key: 'BSZ', label: '百色' },
  { key: 'CHZ', label: '崇左' },
  { key: 'GXL', label: '桂林' },
  { key: 'HEZ', label: '贺州' },
  { key: 'YUL', label: '玉林' },
  { key: 'QNZ', label: '钦州' },
  { key: 'FCG', label: '防城港' },
  { key: 'BHZ', label: '北海' }
]

/** 港代码 → 展示名（未命中时回退为原值） */
function portLabel(key) {
  const p = PORTS.find((x) => x.key === key)
  return p ? p.label : key || ''
}

/** 在列表前追加「全部」这类空值选项，用于筛选器 */
function withAllOption(label) {
  return [{ key: '', label: label }].concat(PORTS)
}

module.exports = { PORTS, portLabel, withAllOption }
