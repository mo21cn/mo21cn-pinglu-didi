#!/usr/bin/env node
/**
 * 小程序 `require` 相对路径静态校验
 * ---------------------------------------------------------------
 * 为什么需要这道门禁：
 *   `pages/entrust/workbench/workbench.js`（3 层目录）曾把 utils 写成
 *   `require('../../utils/entrust')` —— 上两级只到 `pages/`，于是解析成
 *   不存在的 `pages/utils/entrust.js`。小程序运行时表现为**页面白屏 +
 *   console 报 `module ... is not defined`**。
 *
 *   而既有的三道静态门禁**全都抓不到**：
 *     - verify_miniapp.js     检查 JSON / 页面四件套 / tabBar / 可达性
 *     - verify_ui_interactions.js / verify_entrust_ui.js  读源码文本做断言
 *     - verify_frontend_e2e.js  用 requireStub 替换依赖，不做真实路径解析
 *   本步补上「真实存在性」这个维度：路径解析出的目标文件必须真的在磁盘上。
 *
 * 覆盖范围：miniapp 下所有 .js 里以 `.` 开头的 require（npm / 内置包不校验）。
 *
 * 退出码：0 全部存在；1 有引用指向不存在的文件。
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', 'miniapp')
const PATTERN = /require\(\s*['"]([^'"]+)['"]\s*\)/g
const SKIP_DIRS = new Set(['node_modules', 'miniprogram_npm', '.git'])

let scanned = 0
let total = 0
const bad = []

function checkFile(file) {
  scanned += 1
  let src
  try {
    src = fs.readFileSync(file, 'utf8')
  } catch (e) {
    return
  }
  PATTERN.lastIndex = 0
  let m
  while ((m = PATTERN.exec(src)) !== null) {
    const rel = m[1]
    if (!rel.startsWith('.')) continue // 非相对路径：npm 包 / 内置，不在此校验
    total += 1
    const target = path.resolve(path.dirname(file), rel)
    if (!fs.existsSync(target) && !fs.existsSync(target + '.js')) {
      bad.push({
        file: path.relative(ROOT, file).replace(/\\/g, '/'),
        rel: rel,
        resolved: path.relative(ROOT, target).replace(/\\/g, '/')
      })
    }
  }
}

function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name)
    let st
    try {
      st = fs.statSync(p)
    } catch (e) {
      continue
    }
    if (st.isDirectory()) {
      if (!SKIP_DIRS.has(name)) walk(p)
    } else if (name.endsWith('.js')) {
      checkFile(p)
    }
  }
}

walk(ROOT)

console.log(`小程序 require 相对路径校验：扫描 ${scanned} 个 JS，相对 require ${total} 处`)
if (bad.length) {
  console.error(`✗ ${bad.length} 处指向不存在的文件：`)
  bad.forEach((b) => {
    console.error(`   ${b.file}`)
    console.error(`     require('${b.rel}')  →  ${b.resolved}（不存在）`)
  })
  console.error('\n提示：相对层级要按文件所在目录算，例如 pages/a/b/c.js 要回到根需 ../../../')
  process.exit(1)
}
console.log('✓ 全部 require 相对路径都能解析到真实文件')
process.exit(0)
