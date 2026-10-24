<script setup>
import { ref, onMounted } from 'vue'
import {
  listMemories, addMemory, deleteMemory,
  getAgentTools, getPrompts, getAgentPipeline, getAgentMetrics,
} from '../api.js'

const panel = ref('memories')
const loading = ref(false)
const memories = ref([])
const tools = ref([])
const prompts = ref([])
const pipeline = ref(null)
const metrics = ref(null)
const newMemory = ref({ key: '', content: '', category: 'fact' })
const expandedPrompt = ref(null)

const TABS = [
  { key: 'memories', label: '长期记忆', icon: '🧠' },
  { key: 'pipeline', label: '管道/中间件', icon: '🔁' },
  { key: 'prompts', label: '提示词模板', icon: '📝' },
  { key: 'tools', label: '工具注册表', icon: '🔧' },
  { key: 'metrics', label: '性能指标', icon: '📊' },
]

async function loadAll() {
  loading.value = true
  try {
    const [m, t, p, pipe, met] = await Promise.allSettled([
      listMemories(), getAgentTools(), getPrompts(), getAgentPipeline(), getAgentMetrics(),
    ])
    if (m.status === 'fulfilled') memories.value = m.value
    if (t.status === 'fulfilled') tools.value = t.value
    if (p.status === 'fulfilled') prompts.value = p.value
    if (pipe.status === 'fulfilled') pipeline.value = pipe.value
    if (met.status === 'fulfilled') metrics.value = met.value
  } finally {
    loading.value = false
  }
}

async function saveMemory() {
  if (!newMemory.value.key.trim() || !newMemory.value.content.trim()) {
    alert('key / content 不能为空')
    return
  }
  await addMemory(newMemory.value)
  newMemory.value = { key: '', content: '', category: 'fact' }
  memories.value = await listMemories()
}

async function removeMemory(id) {
  if (!confirm('删除该记忆？')) return
  await deleteMemory(id)
  memories.value = await listMemories()
}

onMounted(loadAll)
</script>

<template>
  <section class="page workbench">
    <header class="page-header">
      <div class="page-heading">
        <div class="page-eyebrow">AGENT OBSERVABILITY</div>
        <h1 class="page-title">Agent 工作台</h1>
        <p class="page-subtitle">长期记忆、ReAct 管道、提示词、工具注册表与运行指标</p>
      </div>
      <div class="page-actions">
        <button class="btn btn-secondary" type="button" :disabled="loading" @click="loadAll">
          {{ loading ? '刷新中…' : '↻ 刷新' }}
        </button>
      </div>
    </header>

    <div class="wb-tabs" role="tablist">
      <button
        v-for="t in TABS"
        :key="t.key"
        class="wb-tab"
        :class="{ active: panel === t.key }"
        type="button"
        role="tab"
        :aria-selected="panel === t.key"
        @click="panel = t.key"
      >
        <span>{{ t.icon }}</span>{{ t.label }}
      </button>
    </div>

    <!-- 长期记忆 -->
    <div v-if="panel === 'memories'" class="wb-panel">
      <div class="wb-subtitle">跨会话长期记忆（SQLite 事实表）· 每请求注入系统提示</div>
      <div class="mem-form">
        <input v-model="newMemory.key" class="input" placeholder="key（如 目标岗位）" />
        <input v-model="newMemory.content" class="input" placeholder="content（如 目标岗位是后端开发）" />
        <select v-model="newMemory.category" class="input select">
          <option value="user_profile">user_profile</option>
          <option value="preference">preference</option>
          <option value="decision">decision</option>
          <option value="conclusion">conclusion</option>
          <option value="fact">fact</option>
        </select>
        <button class="btn btn-primary" type="button" @click="saveMemory">＋ 写入</button>
      </div>
      <div v-if="!memories.length" class="wb-empty">暂无长期记忆（回答后会从对话中自动抽取）</div>
      <div v-else class="mem-list">
        <div v-for="m in memories" :key="m.id" class="mem-item">
          <span class="mem-cat">{{ m.category }}</span>
          <span class="mem-key">{{ m.key }}</span>
          <span class="mem-content">{{ m.content }}</span>
          <span class="mem-meta">{{ m.updated_at }} · 命中{{ m.access_count }}</span>
          <button class="icon-btn" type="button" title="删除记忆" aria-label="删除记忆" @click="removeMemory(m.id)">×</button>
        </div>
      </div>
    </div>

    <!-- 管道 / 中间件 -->
    <div v-if="panel === 'pipeline'" class="wb-panel">
      <div class="wb-subtitle">StateGraph 管道（每节点一步，进度经事件槽推送 SSE）</div>
      <template v-if="pipeline">
        <div class="pipe-list">
          <template v-for="(n, i) in pipeline.pipeline" :key="n">
            <div class="pipe-node">
              <span class="pipe-idx">{{ i + 1 }}</span>
              <span>{{ n }}</span>
            </div>
            <span v-if="i < pipeline.pipeline.length - 1" class="pipe-arrow">→</span>
          </template>
        </div>
        <div class="pipe-app">
          <span class="pipe-app-label">应用中间件</span>
          <span v-for="m in pipeline.app_middleware || []" :key="m" class="chip">{{ m }}</span>
        </div>
      </template>
      <div v-else class="wb-empty">加载中…</div>
    </div>

    <!-- 提示词模板 -->
    <div v-if="panel === 'prompts'" class="wb-panel">
      <div class="wb-subtitle">prompts/*.prompt 外部模板（prompt_loader 按名加载）</div>
      <div v-if="!prompts.length" class="wb-empty">暂无模板</div>
      <div v-else class="prompt-list">
        <div v-for="p in prompts" :key="p.name" class="prompt-item">
          <button
            class="prompt-head"
            type="button"
            :aria-expanded="expandedPrompt === p.name"
            @click="expandedPrompt = expandedPrompt === p.name ? null : p.name"
          >
            <span class="prompt-name">{{ p.name }}</span>
            <svg class="chevron" :class="{ open: expandedPrompt === p.name }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          <pre v-if="expandedPrompt === p.name" class="prompt-body">{{ p.content }}</pre>
        </div>
      </div>
    </div>

    <!-- 工具注册表 -->
    <div v-if="panel === 'tools'" class="wb-panel">
      <div class="wb-subtitle">{{ tools.length }} 个自定义工具（官方 StructuredTool）</div>
      <div class="tool-grid">
        <div v-for="t in tools" :key="t.name" class="tool-card">
          <div class="tool-card-name">{{ t.label }} <code>{{ t.name }}</code></div>
          <div class="tool-card-desc">{{ t.description }}</div>
        </div>
      </div>
    </div>

    <!-- 性能指标 -->
    <div v-if="panel === 'metrics'" class="wb-panel">
      <div v-if="metrics" class="metric-grid">
        <div v-for="(v, k) in metrics.performance" :key="k" class="metric-card">
          <div class="metric-key">{{ k }}</div>
          <div class="metric-val">{{ v }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-key">memories.total</div>
          <div class="metric-val">{{ metrics.memories?.total }}</div>
        </div>
      </div>
      <div v-else class="wb-empty">加载中…</div>
    </div>
  </section>
</template>
