<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import SourceCard from './SourceCard.vue'
import { renderMarkdown } from '../markdown.js'

const props = defineProps({
  message: { type: Object, required: true }, // { role, text, sources, status, error, thoughts, toolCalls, retryable }
  canRegenerate: { type: Boolean, default: false },
})
const emit = defineEmits(['retry', 'regenerate'])

const expandedIdx = ref(null)
const listOpen = ref(false) // 引用区折叠开关
const thinkOpen = ref(null) // null = 自动（流式中展开、结束后收起）
const toolOpen = ref(null) // null = 自动（有工具运行中时展开）
const contentEl = ref(null)
const copied = ref(false)
let copyTimer = null

const isAssistant = computed(() => props.message.role === 'assistant')
const isStreaming = computed(
  () => props.message.status !== 'done' && props.message.status !== 'error',
)
const hasThoughts = computed(() => (props.message.thoughts?.length || 0) > 0)
const hasTools = computed(() => (props.message.toolCalls?.length || 0) > 0)
const toolRunning = computed(() =>
  (props.message.toolCalls || []).some((t) => t.status === 'running'),
)
const thinkExpanded = computed(() => (thinkOpen.value === null ? isStreaming.value : thinkOpen.value))
const toolExpanded = computed(() => (toolOpen.value === null ? toolRunning.value : toolOpen.value))

const renderedHtml = computed(() =>
  renderMarkdown(props.message.text || '', { streaming: isStreaming.value }),
)

// 工具名 → 图标 + 中文名（与后端 agent/tools.py TOOL_FRIENDLY 对齐）
const TOOL_META = {
  retrieve_knowledge: { icon: '📚', label: '知识库检索' },
  web_search: { icon: '🌐', label: '联网搜索' },
  fetch_url: { icon: '🔗', label: '抓取网页' },
  rewrite_query: { icon: '✏️', label: '查询改写' },
  hyde_retrieve: { icon: '🧠', label: 'HyDE 检索' },
  multiview_search: { icon: '🔍', label: '多视角检索' },
  evaluate_search_result: { icon: '⚖️', label: '结果评估' },
  generate_title: { icon: '🏷️', label: '生成标题' },
  compress_history: { icon: '🗜️', label: '压缩历史' },
  summarize_conversation: { icon: '📋', label: '总结对话' },
  parse_document: { icon: '📄', label: '文档解析' },
  auto_tag: { icon: '🏷️', label: '自动标签' },
  diagnose_system: { icon: '🩺', label: '系统诊断' },
  analyze_performance: { icon: '📊', label: '性能分析' },
  plan_tasks: { icon: '🗺️', label: '任务拆解' },
  answer: { icon: '💬', label: '回答' },
}

function toolMeta(name) {
  return TOOL_META[name] || { icon: '🛠', label: name || '工具' }
}

function argSnippet(args) {
  try {
    const s = JSON.stringify(args || {})
    return s.length > 80 ? s.slice(0, 80) + '…' : s
  } catch {
    return ''
  }
}

const statusText = computed(() => {
  switch (props.message.status) {
    case 'pending': return '正在检索资料…'
    case 'web_search': return '正在联网搜索…'
    case 'streaming': return '正在生成回答…'
    default: return ''
  }
})

async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return
    }
  } catch {
    /* 降级到 execCommand */
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try { document.execCommand('copy') } catch { /* ignore */ }
  document.body.removeChild(ta)
}

async function copyAnswer() {
  await copyText(props.message.text || '')
  copied.value = true
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copied.value = false }, 1500)
}

// v-html 内容无法绑定 Vue 事件 → 在容器上做事件委托
async function onContentClick(e) {
  const copyBtn = e.target.closest('.code-copy')
  if (copyBtn) {
    const code = copyBtn.closest('.code-block')?.querySelector('code')?.textContent || ''
    await copyText(code)
    copyBtn.textContent = '已复制'
    setTimeout(() => { copyBtn.textContent = '复制' }, 1500)
    return
  }
  const cite = e.target.closest('.cite[data-cite]')
  if (cite) openCitation(Number(cite.dataset.cite))
}

function onContentKeydown(e) {
  if (e.key !== 'Enter' && e.key !== ' ') return
  const cite = e.target.closest('.cite[data-cite]')
  if (!cite) return
  e.preventDefault()
  openCitation(Number(cite.dataset.cite))
}

// 点击回答里的 [n] 徽标：展开引用区、展开对应文件并高亮
function openCitation(index) {
  listOpen.value = true
  expandedIdx.value = index
  nextTick(() => {
    const el = contentEl.value?.querySelector(`.cite[data-cite="${index}"]`)
    if (!el) return
    el.classList.remove('flash')
    void el.offsetWidth // 重启动画
    el.classList.add('flash')
    setTimeout(() => el.classList.remove('flash'), 900)
  })
}

function toggleSource(index) {
  expandedIdx.value = expandedIdx.value === index ? null : index
}

function toggleList() {
  listOpen.value = !listOpen.value
}

onBeforeUnmount(() => clearTimeout(copyTimer))
</script>

<template>
  <!-- 用户消息：右侧浅蓝气泡（纯文本，不渲染 Markdown） -->
  <div v-if="!isAssistant" class="msg is-user">
    <div class="user-bubble">{{ message.text }}</div>
  </div>

  <!-- 助手消息：整宽 Markdown，无气泡 -->
  <div v-else class="msg is-assistant">
    <div class="msg-body">
      <div v-if="statusText && !hasThoughts && !hasTools" class="stream-status">
        <span class="spinner"></span>{{ statusText }}
      </div>

      <!-- Agent 思考过程（流式中自动展开，结束后自动收起） -->
      <div v-if="hasThoughts" class="think-block">
        <button
          class="collapse-head"
          type="button"
          :aria-expanded="thinkExpanded"
          @click="thinkOpen = !thinkExpanded"
        >
          <span class="collapse-title">
            {{ isStreaming ? '正在深度思考' : '已深度思考' }}（{{ message.thoughts.length }} 步）
          </span>
          <span v-if="isStreaming" class="mini-spinner"></span>
          <svg class="chevron" :class="{ open: thinkExpanded }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <div v-if="thinkExpanded" class="think-body">
          <div v-for="(t, i) in message.thoughts" :key="i" class="thought-line">{{ t }}</div>
        </div>
      </div>

      <!-- Agent 工具调用（合并为一组） -->
      <div v-if="hasTools" class="tool-block">
        <button
          class="collapse-head"
          type="button"
          :aria-expanded="toolExpanded"
          @click="toolOpen = !toolExpanded"
        >
          <span class="collapse-title">
            {{ toolRunning ? '正在调用' : '已调用' }} {{ message.toolCalls.length }} 个工具
          </span>
          <span v-if="toolRunning" class="mini-spinner"></span>
          <svg class="chevron" :class="{ open: toolExpanded }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <div v-if="toolExpanded" class="tool-list">
          <div
            v-for="(tc, i) in message.toolCalls"
            :key="i"
            class="tool-row"
            :class="tc.status"
          >
            <span class="tool-icon">{{ toolMeta(tc.name).icon }}</span>
            <span class="tool-name">{{ toolMeta(tc.name).label }}</span>
            <span class="tool-args">{{ argSnippet(tc.args) }}</span>
            <span v-if="tc.status === 'running'" class="mini-spinner"></span>
            <span v-else-if="tc.status === 'error'" class="tool-state err">✗</span>
            <span v-else class="tool-state">✓</span>
            <span v-if="tc.summary" class="tool-summary">{{ tc.summary }}</span>
          </div>
        </div>
      </div>

      <!-- Markdown 回答（[n] 引用与代码复制走事件委托） -->
      <div
        ref="contentEl"
        class="md-body"
        :class="{ 'is-streaming': isStreaming }"
        v-html="renderedHtml"
        @click="onContentClick"
        @keydown="onContentKeydown"
      ></div>

      <div v-if="message.error" class="msg-error">
        {{ message.error }}
        <button v-if="message.retryable" class="retry-btn" type="button" @click="emit('retry')">
          ↻ 重试
        </button>
      </div>

      <!-- 回答操作栏（仅完成后显示） -->
      <div v-if="message.status === 'done'" class="msg-actions">
        <button class="action-btn" type="button" title="复制回答" @click="copyAnswer">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="10" height="10" rx="2" />
            <path d="M5 15V6a2 2 0 0 1 2-2h8" />
          </svg>
          <span>{{ copied ? '已复制' : '复制' }}</span>
        </button>
        <button
          v-if="canRegenerate"
          class="action-btn"
          type="button"
          title="重新生成"
          @click="emit('regenerate')"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 11a8 8 0 1 0-.9 4.4" />
            <path d="M20 5v6h-6" />
          </svg>
          <span>重新生成</span>
        </button>
        <button
          v-if="message.sources && message.sources.length"
          class="action-btn"
          :class="{ active: listOpen }"
          type="button"
          :aria-expanded="listOpen"
          @click="toggleList"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6.5 4h11A1.5 1.5 0 0 1 19 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 18.5v-13A1.5 1.5 0 0 1 6.5 4Z" />
            <path d="M8.5 8h7M8.5 12h7M8.5 16h4" />
          </svg>
          <span>引用来源（{{ message.sources.length }}）</span>
        </button>
      </div>

      <!-- 引用卡片 -->
      <div v-if="listOpen && message.sources && message.sources.length" class="source-list">
        <SourceCard
          v-for="s in message.sources"
          :key="s.index"
          :source="s"
          :expanded="expandedIdx === s.index"
          @toggle="toggleSource(s.index)"
        />
      </div>
    </div>
  </div>
</template>
