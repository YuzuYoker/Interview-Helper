// Markdown 渲染管线：markdown-it + highlight.js + DOMPurify
// 回答里的 [n] 引用会转成可点击的 <span class="cite" data-cite="n">，
// 点击事件由 MessageBubble 在容器上做事件委托（v-html 无法绑定 Vue 事件）。
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  html: false, // 模型输出的原始 HTML 一律转义
  linkify: true,
  breaks: true,
  typographer: false,
})

// ---- [n] 引用标记（只在行内文本生效，代码块/行内代码不会命中） ----
function citeRule(state, silent) {
  if (state.src.charCodeAt(state.pos) !== 0x5b /* [ */) return false

  const match = state.src.slice(state.pos).match(/^\[(\d{1,3})\]/)
  if (!match) return false

  const end = state.pos + match[0].length
  const prev = state.pos > 0 ? state.src[state.pos - 1] : ''
  const next = state.src[end]

  // 排除 ![1]（图片）、[1](url)（数字链接）、[1][ref]（引用式链接）
  if (prev === '!') return false
  if (next === '(' || next === '[') return false

  if (!silent) {
    const token = state.push('cite_ref', '', 0)
    token.content = match[0]
    token.meta = { index: Number(match[1]) }
  }
  state.pos = end
  return true
}
md.inline.ruler.before('link', 'cite_ref', citeRule)

md.renderer.rules.cite_ref = (tokens, idx) => {
  const n = tokens[idx].meta?.index ?? 1
  return `<span class="cite" data-cite="${n}" role="button" tabindex="0">[${n}]</span>`
}

// ---- 代码块：语言标签 + 复制按钮（流式期间跳过高亮，完成后才着色） ----
md.renderer.rules.fence = (tokens, idx, options, env) => {
  const token = tokens[idx]
  const info = (token.info || '').trim().split(/\s+/)[0]
  const lang = info || 'text'
  const code = token.content
  const canHighlight = env?.highlight !== false && info && hljs.getLanguage(info)

  let highlighted
  if (canHighlight) {
    try {
      highlighted = hljs.highlight(code, { language: info, ignoreIllegals: true }).value
    } catch {
      highlighted = md.utils.escapeHtml(code)
    }
  } else {
    highlighted = md.utils.escapeHtml(code)
  }

  return (
    `<div class="code-block" data-lang="${md.utils.escapeHtml(lang)}">` +
    `<div class="code-head"><span class="code-lang">${md.utils.escapeHtml(lang)}</span>` +
    `<button class="code-copy" type="button">复制</button></div>` +
    `<pre><code class="hljs">${highlighted}</code></pre></div>`
  )
}

// ---- 外链统一新窗口打开 ----
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

const PURIFY_OPTIONS = {
  ADD_ATTR: ['target', 'rel', 'data-cite', 'data-lang', 'role', 'tabindex'],
  FORBID_TAGS: ['style', 'script', 'iframe', 'form', 'input', 'textarea', 'select'],
}

export function renderMarkdown(source, { streaming = false } = {}) {
  const text = source || ''
  if (!text) return ''
  const html = md.render(text, { highlight: !streaming })
  return DOMPurify.sanitize(html, PURIFY_OPTIONS)
}
