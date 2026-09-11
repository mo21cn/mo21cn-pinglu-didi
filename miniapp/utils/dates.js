// 本地日期工具（P2-3 修复引入）
//
// ⚠️ 为什么不用 `new Date(...).toISOString().slice(0, 10)`：
//   toISOString 输出 UTC——北京时间 00:00–08:00 区间会取到「昨天」，
//   「明天 / 下一天」默认值存在偏移（第三方审计 P2-3，全站 7 处）。
//   业务日期一律走本文件的本地时区格式化。

/** Date → 'yyyy-MM-dd'（本地时区；入参缺省取当前时间） */
function fmtDate(d) {
  const dt = (d instanceof Date) ? d : new Date(d || Date.now())
  const y = dt.getFullYear()
  const m = String(dt.getMonth() + 1).padStart(2, '0')
  const day = String(dt.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 偏移 n 天的本地日期（n 可为小数天前的负数，一般传整数） */
function fmtDateOffset(days, base) {
  const b = (base === undefined) ? Date.now() : base
  return fmtDate(new Date(b + days * 86400000))
}

module.exports = { fmtDate, fmtDateOffset }
