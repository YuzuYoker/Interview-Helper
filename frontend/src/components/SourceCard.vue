<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  source: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])

const showFull = ref(false)
const PREVIEW_LEN = 300

// 用户上传的附件：显示"附件"标签而非匹配度（分数语义不同，0.003 显示成 0% 会误导）
const isAttachment = computed(() => props.source.is_attachment)
// 联网搜索结果：显示 🌐 标签 + 原文链接，同样不显示置信度
const isWeb = computed(() => props.source.is_web)

const scoreClass = computed(() => {
  const s = props.source.score
  if (s >= 0.7) return 'high'
  if (s >= 0.4) return 'mid'
  return 'low'
})

const scoreText = computed(() => {
  const pct = props.source.score * 100
  if (pct >= 1) return `${Math.round(pct)}%`
  return '<1%'
})

const content = computed(() => {
  const c = props.source.content || ''
  return showFull.value || c.length <= PREVIEW_LEN ? c : c.slice(0, PREVIEW_LEN) + '……'
})
</script>

<template>
  <div class="source-card">
    <div class="source-head" @click="emit('toggle')">
      <span class="idx">{{ source.index }}</span>
      <span class="file">{{ source.filename }}{{ source.page ? ` · 第${source.page}页` : '' }}</span>
      <span v-if="isAttachment" class="tag">📎 附件</span>
      <span v-else-if="isWeb" class="tag web">🌐 网页</span>
      <span v-else class="score">
        <span class="score-dot" :class="scoreClass" :title="`置信度 ${scoreText}`" />
        {{ scoreText }}
      </span>
      <span class="score"><span v-if="expanded">▾</span><span v-else>▸</span></span>
    </div>
    <div v-if="expanded" class="source-body">
      <a
        v-if="source.url"
        class="web-link"
        :href="source.url"
        target="_blank"
        rel="noopener noreferrer"
      >打开原文 ↗</a>
      <pre>{{ content }}</pre>
      <button
        v-if="(source.content || '').length > PREVIEW_LEN"
        class="more-btn"
        @click="showFull = !showFull"
      >
        {{ showFull ? '收起' : '查看全文' }}
      </button>
    </div>
  </div>
</template>
