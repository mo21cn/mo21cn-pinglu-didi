#!/usr/bin/env node
/**
 * 小程序静态校验（前端无自动化测试时的兜底）
 *
 * 检查项：
 *   1. 所有 .json 语法可解析
 *   2. app.json 注册的页面四件套（.js/.json/.wxml/.wxss）齐全
 *   3. tabBar（含 custom）配置合法：≤5 项、页面已注册、custom 组件文件存在
 *   4. 路由引用可达：页面/wxml 中出现的 /pages/... 必须在 app.json 注册
 *   5. tabBar 页面用 switchTab、非 tabBar 页面用 navigateTo（错用会导致静默失败）
 *
 * 用法：node scripts/verify_miniapp.js
 * 退出码：0 通过 / 1 有问题
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', 'miniapp')
let errors = []
const checked = { json: 0, js: 0, routes: 0 }

function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name)
    let st
    try { st = fs.statSync(p) } catch (e) { continue }
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === '.git') continue
      walk(p, out)
    } else out.push(p)
  }
  return out
}

const files = walk(ROOT)

// ---- 1. JSON 语法 ----
for (const f of files.filter((x) => x.endsWith('.json'))) {
  try {
    JSON.parse(fs.readFileSync(f, 'utf8'))
    checked.json++
  } catch (e) {
    errors.push(`[JSON] ${path.relative(ROOT, f)}: ${e.message}`)
  }
}

// ---- 2. app.json 注册 & tabBar ----
const appJson = JSON.parse(fs.readFileSync(path.join(ROOT, 'app.json'), 'utf8'))
const pages = new Set(appJson.pages || [])
const tabBarPages = new Set(((appJson.tabBar && appJson.tabBar.list) || []).map((t) => t.pagePath))

for (const p of pages) {
  for (const ext of ['.js', '.json', '.wxml', '.wxss']) {
    if (!fs.existsSync(path.join(ROOT, p + ext))) errors.push(`[PAGE] 缺少文件 ${p}${ext}`)
  }
}

if (appJson.tabBar && appJson.tabBar.custom) {
  for (const ext of ['.js', '.json', '.wxml', '.wxss']) {
    if (!fs.existsSync(path.join(ROOT, 'custom-tab-bar', 'index' + ext))) {
      errors.push(`[TABBAR] 缺少 custom-tab-bar/index${ext}`)
    }
  }
}

if (tabBarPages.size > 5) errors.push(`[TABBAR] list 超过 5 项（${tabBarPages.size}）`)
for (const t of tabBarPages) {
  if (!pages.has(t)) errors.push(`[TABBAR] tabBar 页面未在 pages 注册: ${t}`)
}

// ---- 3. 路由可达性 ----
// 路由必须以字母数字结尾（避免把 indexOf('/pages/xxx/') 这类前缀判断误当路由）
const routeRe = /['"`]\/pages\/[A-Za-z0-9_\/-]*[A-Za-z0-9_-]/g
const switchTabRe = /switchTab\s*\(\s*\{\s*url:\s*['"`](\/pages\/[^'"`?]+)/g

for (const f of files.filter((x) => x.endsWith('.js') || x.endsWith('.wxml'))) {
  const src = fs.readFileSync(f, 'utf8')
  const rel = path.relative(ROOT, f)

  let m
  routeRe.lastIndex = 0
  while ((m = routeRe.exec(src))) {
    const before = src.slice(Math.max(0, m.index - 32), m.index)
    if (/indexOf\s*\(\s*$|startsWith\s*\(\s*$/.test(before)) continue
    const route = m[0].slice(1).replace(/^\//, '')
    checked.routes++
    if (!pages.has(route)) errors.push(`[ROUTE] ${rel} 引用了未注册页面: /${route}`)
  }

  let s
  switchTabRe.lastIndex = 0
  while ((s = switchTabRe.exec(src))) {
    const route = s[1].replace(/^\//, '')
    if (!tabBarPages.has(route)) {
      errors.push(`[ROUTE] ${rel} switchTab 到非 tabBar 页: ${s[1]}（应使用 navigateTo）`)
    }
  }
}

// ---- 输出 ----
console.log(`检查完成：JSON ${checked.json} 个 / 路由引用 ${checked.routes} 处 / 页面 ${pages.size} 个`)
if (errors.length) {
  console.log(`\n发现 ${errors.length} 个问题：`)
  errors.forEach((e) => console.log('  - ' + e))
  process.exit(1)
}
console.log('全部通过 ✓')
