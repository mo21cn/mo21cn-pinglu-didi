#!/usr/bin/env node
/**
 * 小程序静态校验（前端无自动化测试时的兜底）
 *
 * 检查项：
 *   1. 所有 .json 语法可解析
 *   2. app.json 注册的页面四件套（.js/.json/.wxml/.wxss）齐全
 *   3. 页面路径规范（pages/ 下、文件名与所在目录同名）＋ 无重复注册
 *   4. tabBar（含 custom）配置合法：≤5 项、页面已注册、custom 组件文件存在
 *   5. 路由引用可达：页面/wxml 中出现的 /pages/... 必须在 app.json 注册
 *   6. tabBar 页面用 switchTab、非 tabBar 页面用 navigateTo（错用会导致静默失败）
 *   7. wxml 事件绑定的方法在同名 .js 中已定义（抓事件名写错/漏定义）
 *
 * 用法：node scripts/verify_miniapp.js
 * 退出码：0 通过 / 1 有问题
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', 'miniapp')
let errors = []
const checked = { json: 0, js: 0, routes: 0 }

// 个人/本机私有文件：不入库，也不计入统计（否则本地与 CI 的数量对不上）
const IGNORE_FILES = new Set(['project.private.config.json'])

function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name)
    let st
    try { st = fs.statSync(p) } catch (e) { continue }
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === '.git') continue
      walk(p, out)
    } else if (!IGNORE_FILES.has(name)) out.push(p)
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

// 页面路径规范：必须在 pages/ 下，且文件名与所在目录同名（pages/<域>/<页>/<页>）
for (const p of pages) {
  const base = path.basename(p)
  if (p.split('/')[0] !== 'pages') errors.push(`[CONV] 页面不在 pages/ 下: ${p}`)
  if (base !== path.basename(path.dirname(p))) {
    errors.push(`[CONV] 路径与文件名不一致: ${p}（应为 .../${base}/${base}）`)
  }
}

const dupPages = Array.from(new Set((appJson.pages || []).filter((p, i, arr) => arr.indexOf(p) !== i)))
if (dupPages.length) errors.push(`[PAGE] 重复注册: ${dupPages.join(', ')}`)

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

// ---- 4. wxml 事件处理函数存在性（抓「事件名写错/方法漏定义」这类静默失败） ----
const handlerRe = /\b(?:capture-)?(?:bind|catch)(?::)?([a-zA-Z]+)\s*=\s*"([^"]*)"/g
checked.handlers = 0

for (const f of files.filter((x) => x.endsWith('.wxml'))) {
  const jsPath = f.replace(/\.wxml$/, '.js')
  if (!fs.existsSync(jsPath)) continue
  const wxml = fs.readFileSync(f, 'utf8')
  const js = fs.readFileSync(jsPath, 'utf8')
  const rel = path.relative(ROOT, f)

  let h
  handlerRe.lastIndex = 0
  while ((h = handlerRe.exec(wxml))) {
    const name = (h[2] || '').trim()
    if (!name || name.indexOf('{{') !== -1) continue
    checked.handlers++
    const defined = new RegExp('(^|[^\\w.$])' + name + '\\s*[(:]').test(js)
    if (!defined) errors.push(`[EVENT] ${rel} 绑定了未定义的方法: ${name}`)
  }
}

// ---- 输出 ----
console.log(
  `检查完成：JSON ${checked.json} 个 / 页面 ${pages.size} 个 / 路由引用 ${checked.routes} 处 / 事件绑定 ${checked.handlers} 处`
)
if (errors.length) {
  console.log(`\n发现 ${errors.length} 个问题：`)
  errors.forEach((e) => console.log('  - ' + e))
  process.exit(1)
}
console.log('全部通过 ✓')
