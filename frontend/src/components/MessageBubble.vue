<script setup>
import { computed, ref } from 'vue'
import SourceCard from './SourceCard.vue'

const props = defineProps({
  message: { type: Object, required: true }, // { role, text, sources, status, error }
})

const expandedIdx = ref(null)
const listOpen = ref(false) // 引用区折叠开关（参考 DeepSeek 官网：默认收起为一行，点击展开文件）

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
      <div v-if="message.error" class="msg-error">{{ message.error }}</div>
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
