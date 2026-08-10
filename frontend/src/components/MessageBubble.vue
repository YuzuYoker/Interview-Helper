<script setup>
import { computed, ref } from 'vue'
import SourceCard from './SourceCard.vue'

const props = defineProps({
  message: { type: Object, required: true }, // { role, text, sources, status, error, thoughts, toolCalls, retryable }
})
defineEmits(['retry'])

const expandedIdx = ref(null)
const listOpen = ref(false) // 引用区折叠开关（参考 DeepSeek 官网：默认收起为一行，点击展开文件）
const thinkOpen = ref(false) // 思考过程折叠开关

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

// 把 answer 中的 [n] 引用标记切分为文本段 + 徽标段
const segments = computed(() => {
  const parts = []
  const re = /\[(\d+)\]/g
  let last = 0
  let m
  const text = props.message.text || ''
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push({ type: 'text', value: text.slice(last, m.index) })
    parts.push({ type: 'cite', value: Number(m[1]) })
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push({ type: 'text', value: text.slice(last) })
  return parts
})

const statusText = computed(() => {
  switch (props.message.status) {
    case 'pending': return '正在检索资料…'
    case 'web_search': return '正在联网搜索…' // 开关开启时：先抓最新网页再生成
    case 'streaming': return '正在生成回答…'
    default: return ''
  }
})

// 点击回答里的 [n] 徽标：展开引用区并展开对应文件
function onCiteClick(idx) {
  listOpen.value = true
  expandedIdx.value = expandedIdx.value === idx ? null : idx
}

function toggleSource(idx) {
  expandedIdx.value = expandedIdx.value === idx ? null : idx
}
</script>

<template>
  <div class="msg" :class="message.role">
    <div class="msg-avatar">{{ message.role === 'user' ? '我' : 'AI' }}</div>
    <div class="msg-body">
      <!-- 生成状态胶囊（检索中 → 生成中），DeepSeek 风格 -->
      <div v-if="statusText" class="status-pill">
        <span class="spinner"></span>{{ statusText }}
      </div>

      <!-- Agent 思考过程（可折叠，默认收起） -->
      <div v-if="message.thoughts && message.thoughts.length" class="agent-think">
        <div class="agent-toggle" @click="thinkOpen = !thinkOpen">
          思考过程（{{ message.thoughts.length }}）<span>{{ thinkOpen ? '▾' : '▸' }}</span>
        </div>
        <div v-if="thinkOpen" class="thought-list">
          <div v-for="(t, i) in message.thoughts" :key="i" class="thought-item">{{ t }}</div>
        </div>
      </div>

      <!-- Agent 工具调用卡片 -->
      <div v-if="message.toolCalls && message.toolCalls.length" class="tool-cards">
        <div v-for="(tc, i) in message.toolCalls" :key="i" class="tool-card" :class="tc.status">
          <span class="tc-icon">{{ toolMeta(tc.name).icon }}</span>
          <span class="tc-name">{{ toolMeta(tc.name).label }}</span>
          <span class="tc-args">{{ argSnippet(tc.args) }}</span>
          <span class="tc-status">{{ tc.status === 'running' ? '⏳' : tc.status === 'error' ? '✗' : '✓' }}</span>
          <span v-if="tc.summary" class="tc-summary">{{ tc.summary }}</span>
        </div>
      </div>

      <div class="msg-content">
        <template v-for="(seg, i) in segments" :key="i">
          <span v-if="seg.type === 'text'">{{ seg.value }}</span>
          <span
            v-else
            class="cite"
            :class="{ flash: expandedIdx === seg.value }"
            @click="onCiteClick(seg.value)"
          >[{{ seg.value }}]</span>
        </template>
        <span v-if="message.status === 'streaming'" class="msg-cursor"></span>
      </div>
      <div v-if="message.error" class="msg-error">
        {{ message.error }}
        <button v-if="message.retryable" class="retry-btn" @click="$emit('retry')">↻ 重试</button>
      </div>
      <!-- 引用区：默认收起为一行，点击展开文件列表（DeepSeek 风格） -->
      <div v-if="message.sources && message.sources.length" class="source-panel">
        <div
          class="source-toggle"
          :class="{ open: listOpen }"
          @click="listOpen = !listOpen"
        >
          <span class="st-dot"></span>
          引用来源（{{ message.sources.length }}）
          <span class="st-arrow">{{ listOpen ? '▾' : '▸' }}</span>
        </div>
        <div v-if="listOpen" class="source-list">
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
  </div>
</template>
