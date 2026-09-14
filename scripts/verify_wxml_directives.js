#!/usr/bin/env node
/**
 * 小程序 WXML 编译期禁形静态校验：`wx:if` / `wx:for` 同元素 + 兄弟 `wx:else`
 * ---------------------------------------------------------------
 * 为什么需要这道门禁：
 *   `pages/entrust/case/case.wxml` 把 `wx:if="{{item.rows.length}}` 与
 *   `wx:for="{{item.rows}}"` 写在**同一个元素**上，紧随其后又写了兄弟 `wx:else`。
 *   微信编译器会把 `wx:if` 卷进循环作用域，那个 `wx:else` 于是绑不到任何 `wx:if`，
 *   报：
 *
 *     Bad attr `wx:elif` with message: `wx:if not found, then something must be wrong`.
 *     编译 .wxml 文件错误 … /pages/entrust/case/case.wxml#89
 *
 *   后果不是"这一块不渲染"，而是**整个 wxml 编译失败、整页被错误覆盖层替换** ——
 *   在真机上看起来就是"页面白屏"，而页面 JS 与后端全是好的。
 *
 *   既有的每一道门禁都抓不到它，原因是覆盖面而不是疏漏：
 *     - verify_miniapp.js          检查 JSON / 页面四件套 / tabBar / 可达性
 *     - verify_ui_interactions.js  用 `new Function` 加载页面 **JS**，不解析 WXML
 *     - verify_entrust_ui.js       读源码**文本**做断言（比字符串）
 *     - verify_frontend_e2e.js     跑后端事实，不经过编译
 *   ⇒ 与 `verify_require_paths.js` 同源：真机走查不可被静态校验替代，
 *     但**已经被真机抓到过的禁形，必须补一道静态闸**，否则下次还会再犯。
 *
 * 覆盖范围：miniapp 下所有 .wxml 里同级的 `wx:if` / `wx:elif` / `wx:else` 链。
 * 判据（保守，只报确定会编译失败的）：
 *   对每个带 `wx:else` / `wx:elif` 的元素，**回溯整条 elif 链**：
 *     1. 链首必须是 `wx:if`；
 *     2. 链上任何一环带 `wx:for` —— 该环的 `wx:if` 被卷进循环作用域，链就断了。
 *   ⚠️ 只查"前一个兄弟"是错的：`wx:elif` 跟 `wx:elif` 本身合法（else-if 链），
 *      那样会把所有合法的 elif 链全部误报。
 *   ⚠️ `wx:else` 是**无值属性**（没有 `="…"`），只按 `key="value"` 抽属性会漏掉它，
 *      于是扫描器恒 0 命中、看起来像"仓库很干净"。两条都必须按无值属性再抽一遍。
 *
 * 退出码：0 全部合规；1 存在编译期禁形。
 */
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', 'miniapp')
const SKIP_DIRS = new Set(['node_modules', 'miniprogram_npm', '.git'])
const TAG_RE = /<(\/?)([A-Za-z][-\w]*)((?:\s[^<>]*?)?)(\/?)>/gs
const ATTR_RE = /([\w:.-]+)\s*=\s*"([^"]*)"/g
const BARE_RE = /(?:^|\s)(wx:[\w-]+)(?=\s|=|$)/g

let scanned = 0
let total = 0
const bad = []

function attrsOf(raw) {
  const attrs = {}
  let m
  ATTR_RE.lastIndex = 0
  while ((m = ATTR_RE.exec(raw)) !== null) attrs[m[1]] = m[2]
  BARE_RE.lastIndex = 0
  while ((m = BARE_RE.exec(raw)) !== null) {
    if (!(m[1] in attrs)) attrs[m[1]] = ''
  }
  return attrs
}

function checkFile(file) {
  scanned += 1
  let src
  try {
    src = fs.readFileSync(file, 'utf8')
  } catch (e) {
    return
  }
  const rel = path.relative(ROOT, file).replace(/\\/g, '/')
  // 每层一个"已出现的同级元素"列表，用来回溯 if/elif 链
  const stack = [[]]
  TAG_RE.lastIndex = 0
  let m
  while ((m = TAG_RE.exec(src)) !== null) {
    const closing = m[1]
    const name = m[2]
    const selfClose = m[4]
    const line = src.slice(0, m.index).split('\n').length
    if (closing) {
      if (stack.length > 1) stack.pop()
      continue
    }
    const attrs = attrsOf(m[3] || '')
    const siblings = stack[stack.length - 1]
    const isElse = 'wx:else' in attrs
    const isElif = 'wx:elif' in attrs

    if (siblings.length && (isElse || isElif)) {
      total += 1
      const chain = [{ tag: name, attrs: attrs, line: line }]
      let i = siblings.length - 1
      while (i >= 0 && 'wx:elif' in siblings[i].attrs) {
        chain.push(siblings[i])
        i -= 1
      }
      if (i < 0 || !('wx:if' in siblings[i].attrs)) {
        const head = i >= 0 ? siblings[i] : null
        bad.push({
          file: rel,
          line: line,
          what: isElse ? 'wx:else' : 'wx:elif',
          why: head
            ? `链首 <${head.tag}>（第 ${head.line} 行）没有 wx:if`
            : '前面没有兄弟元素'
        })
      } else {
        chain.push(siblings[i])
        const looper = chain.find((n) => 'wx:for' in n.attrs)
        if (looper) {
          bad.push({
            file: rel,
            line: line,
            what: isElse ? 'wx:else' : 'wx:elif',
            why:
              `if/elif 链上 <${looper.tag}>（第 ${looper.line} 行）同时带 wx:for，` +
              '微信编译器会把 wx:if 卷进循环作用域'
          })
        }
      }
    }
    siblings.push({ tag: name, attrs: attrs, line: line })
    if (!selfClose) stack.push([])
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
    } else if (name.endsWith('.wxml')) {
      checkFile(p)
    }
  }
}

walk(ROOT)

console.log(`小程序 WXML 条件/循环禁形校验：扫描 ${scanned} 个 wxml，检查 ${total} 处 wx:else / wx:elif`)
if (bad.length) {
  console.error(`✗ ${bad.length} 处会导致 wxml 编译失败：`)
  bad.forEach((b) => {
    console.error(`   ${b.file}:${b.line}  <${b.what}> —— ${b.why}`)
  })
  console.error('\n修法：条件与循环分两层 —— 外层 <block wx:if="{{...length}}"> 承担条件，')
  console.error('      内层元素只留 wx:for，兄弟 wx:else 才绑得到那个 wx:if。')
  console.error('症状提示：这类错误**不进模拟器 console**，只在开发者工具的编译覆盖层里，')
  console.error('      真机上表现为整页"白屏"（真机走查 30 实测）。')
  process.exit(1)
}
console.log('✓ 全部 wx:if / wx:elif / wx:else 链都能编译通过')
process.exit(0)
