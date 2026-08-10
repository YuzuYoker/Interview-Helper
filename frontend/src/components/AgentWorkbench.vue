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
  <div class="workbench">
    <div class="wb-header">
      <div class="wb-title">🛠 Agent 工作台</div>
      <button class="wb-refresh" :disabled="loading" @click="loadAll">↻ 刷新</button>
    </div>

    <div class="wb-tabs">
      <button :class="{ active: panel === 'memories' }" @click="panel = 'memories'">🧠 长期记忆</button>
      <button :class="{ active: panel === 'pipeline' }" @click="panel = 'pipeline'">🔁 管道/中间件</button>
      <button :class="{ active: panel === 'prompts' }" @click="panel = 'prompts'">📝 提示词模板</button>
      <button :class="{ active: panel === 'tools' }" @click="panel = 'tools'">🔧 工具注册表</button>
      <button :class="{ active: panel === 'metrics' }" @click="panel = 'metrics'">📊 性能指标</button>
    </div>

    <!-- 长期记忆 -->
    <div v-if="panel === 'memories'" class="wb-panel">
      <div class="wb-subtitle">跨会话长期记忆（SQLite 事实表）· 每请求注入系统提示</div>
      <div class="mem-form">
        <input v-model="newMemory.key" placeholder="key（如 目标岗位）" />
        <input v-model="newMemory.content" placeholder="content（如 目标岗位是后端开发）" />
        <select v-model="newMemory.category">
          <option value="user_profile">user_profile</option>
          <option value="preference">preference</option>
          <option value="decision">decision</option>
          <option value="conclusion">conclusion</option>
          <option value="fact">fact</option>
        </select>
        <button @click="saveMemory">＋ 写入</button>
      </div>
      <div v-if="!memories.length" class="wb-empty">暂无长期记忆（回答后会从对话中自动抽取）</div>
      <div v-else class="mem-list">
        <div v-for="m in memories" :key="m.id" class="mem-item">
          <span class="mem-cat">{{ m.category }}</span>
          <span class="mem-key">{{ m.key }}</span>
          <span class="mem-content">{{ m.content }}</span>
          <span class="mem-meta">{{ m.updated_at }} · 命中{{ m.access_count }}</span>
          <button class="mem-del" title="删除" @click="removeMemory(m.id)">×</button>
        </div>
      </div>
    </div>

    <!-- 管道 / 中间件 -->
    <div v-if="panel === 'pipeline'" class="wb-panel">
      <div class="wb-subtitle">StateGraph 管道（每节点一步，进度经事件槽推送 SSE）</div>
      <div v-if="pipeline" class="pipe-list">
        <div class="pipe-node" v-for="(n, i) in pipeline.pipeline" :key="n">
          <span class="pipe-idx">{{ i + 1 }}</span>{{ n }}
          <span class="pipe-arrow" v-if="i < pipeline.pipeline.length - 1">→</span>
        </div>
        <div class="pipe-app">应用中间件：{{ (pipeline.app_middleware || []).join(' / ') }}</div>
      </div>
      <div v-else class="wb-empty">加载中…</div>
    </div>

    <!-- 提示词模板 -->
    <div v-if="panel === 'prompts'" class="wb-panel">
      <div class="wb-subtitle">prompts/*.prompt 外部模板（prompt_loader 按名加载）</div>
      <div v-if="!prompts.length" class="wb-empty">暂无模板</div>
      <div v-else class="prompt-list">
        <div v-for="p in prompts" :key="p.name" class="prompt-item">
          <div class="prompt-head" @click="expandedPrompt = expandedPrompt === p.name ? null : p.name">
            <span class="prompt-name">{{ p.name }}</span>
            <span>{{ expandedPrompt === p.name ? '▾' : '▸' }}</span>
          </div>
          <pre v-if="expandedPrompt === p.name" class="prompt-body">{{ p.content }}</pre>
        </div>
      </div>
    </div>

    <!-- 工具注册表 -->
    <div v-if="panel === 'tools'" class="wb-panel">
      <div class="wb-subtitle">{{ tools.length }} 个自定义工具（官方 StructuredTool）</div>
      <div class="tool-grid">
        <div v-for="t in tools" :key="t.name" class="tool-card-wb">
          <div class="tool-name">{{ t.label }} <code>{{ t.name }}</code></div>
          <div class="tool-desc">{{ t.description }}</div>
        </div>
      </div>
    </div>

    <!-- 性能指标 -->
    <div v-if="panel === 'metrics'" class="wb-panel">
      <div v-if="metrics" class="metric-cards">
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
  </div>
</template>
