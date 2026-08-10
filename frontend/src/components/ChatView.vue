<script setup>
import { ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { chatStream, uploadDocument, getUploadStatus, createConversation, getConversation } from '../api.js'
import MessageBubble from './MessageBubble.vue'

const props = defineProps({
  conversationId: { type: String, default: null }, // App 传入：当前会话（null = 新对话）
})
const emit = defineEmits(['created', 'updated'])

const messages = ref([])
const input = ref('')
const streaming = ref(false)
const listEl = ref(null)
const fileInput = ref(null)
const attachments = ref([]) // { name, status: 'uploading'|'done'|'err', message }
const uploadedCount = ref(0) // 本次对话已提交文件数（上限 5）
const convId = ref(props.conversationId)
const webOn = ref(false) // 「🌐 联网搜索」开关（Agent 模式下仅作系统提示 hint，是否联网由 LLM 决策）
const lastQuestion = ref('') // 重试用：记住最近一次问题
let controller = null

// ---- 上传限制：每对话 ≤5 个文件，单个 ≤10MB ----
const MAX_FILES_PER_CHAT = 5
const MAX_FILE_SIZE = 10 * 1024 * 1024
const SUPPORTED_FORMATS = 'PDF、DOCX、XLSX、TXT、JPG/JPEG/PNG/BMP/WEBP（图片走 OCR 识别）'

// ---- 打开已有会话：从服务端恢复完整上下文 ----
async function loadConversation() {
  if (!props.conversationId) return
  try {
    const conv = await getConversation(props.conversationId)
    convId.value = conv.id
    messages.value = (conv.messages || []).map((m) => {
      // 历史消息带 tool_trace → 重建工具卡片（tool_call/tool_result 配对）
      const toolCalls = []
      ;(m.tool_trace || []).forEach((entry) => {
        if (entry.type === 'tool_call') {
          toolCalls.push({
            id: entry.tool_call_id,
            name: entry.name,
            args: entry.args || {},
            status: 'done',
            summary: '',
          })
        } else if (entry.type === 'tool_result') {
          const card = toolCalls.find((c) => c.id === entry.tool_call_id)
          if (card) {
            card.status = entry.ok ? 'done' : 'error'
            card.summary = entry.summary || ''
          }
        }
      })
      return {
        role: m.role,
        text: m.content,
        sources: m.sources || [],
        status: 'done', // 历史消息都是已完成状态，不再显示生成胶囊
        error: '',
        thoughts: [],
        toolCalls,
        retryable: false,
      }
    })
  } catch (e) {
    console.error('加载会话失败', e)
  }
}
onMounted(loadConversation)

// ---- 对话内上传（+ 按钮，类 Kimi/豆包交互）----
async function uploadFiles(files) {
  for (const file of files) {
    if (file.size > MAX_FILE_SIZE) {
      attachments.value.push({
        name: file.name,
        status: 'err',
        message: `超过 10MB 上限（实际 ${(file.size / 1048576).toFixed(1)}MB）`,
      })
      continue // 超限文件不入队、不计数
    }
    const item = { name: file.name, status: 'uploading', message: '上传中…' }
    attachments.value.push(item)
    uploadedCount.value += 1
    try {
      const r = await uploadDocument(file)
      item.status = 'uploading'
      item.message = '后台向量化中…'
      for (let i = 0; i < 300; i++) {
        await new Promise((res) => setTimeout(res, 2000))
        const st = await getUploadStatus(r.document_id)
        if (st.status === 'done') {
          item.status = 'done'
          item.message = `已入库（${st.chunk_count} 个分块），现在可以直接问关于它的问题了`
          break
        }
        if (st.status === 'error') {
          item.status = 'err'
          item.message = st.error || '处理失败'
          break
        }
      }
    } catch (e) {
      item.status = 'err'
      item.message = e.message || String(e)
    }
    if (item.status === 'err') uploadedCount.value -= 1 // 失败回滚名额
  }
  setTimeout(() => {
    attachments.value = attachments.value.filter((a) => a.status !== 'done')
  }, 8000)
}

function onPick(e) {
  const files = [...e.target.files]
  if (uploadedCount.value + files.length > MAX_FILES_PER_CHAT) {
    alert(`每次对话最多上传 ${MAX_FILES_PER_CHAT} 个文件，当前已上传 ${uploadedCount.value} 个`)
    e.target.value = ''
    return
  }
  uploadFiles(files)
  e.target.value = ''
}

// ---- 对话 ----
// 打字机：rAF 节流，每帧至多一次 DOM 更新
let pending = ''
let rafId = null
let currentMsg = null

function pushDelta(text) {
  pending += text
  if (rafId) return
  rafId = requestAnimationFrame(() => {
    rafId = null
    if (currentMsg) {
      currentMsg.text += pending
      scrollToBottom()
    }
    pending = ''
  })
}

// 智能滚动（参考 DeepSeek 官网）：发送新消息时强制到底；
// 流式生成中只有用户本来就停留在底部附近（≤120px）才自动跟随，
// 用户往上翻看时不再被强制拉到底部。
function scrollToBottom(force = false) {
  nextTick(() => {
    const el = listEl.value
    if (!el) return
    if (force) {
      el.scrollTop = el.scrollHeight
    } else {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
      if (nearBottom) el.scrollTop = el.scrollHeight
    }
  })
}

function buildHistory() {
  return messages.value
    .filter((m) => m.role === 'assistant' ? m.status === 'done' : true)
    .slice(-8)
    .map((m) => ({ role: m.role, content: m.text }))
}

async function send() {
  const question = input.value.trim()
  if (!question || streaming.value) return
  input.value = ''
  lastQuestion.value = question // 记录最近问题，失败可重试

  // 新对话：先建会话拿 id（失败则无持久化，本次对话仍可继续）
  if (!convId.value) {
    try {
      const c = await createConversation()
      convId.value = c.id
      emit('created', c.id)
    } catch (e) {
      console.error('创建会话失败，本次对话不持久化', e)
    }
  }

  messages.value.push({ role: 'user', text: question, sources: [], status: 'done' })
  // 开关开启时先显示"正在联网搜索…"；收到 sources 事件后转为"正在生成回答…"
  const assistant = {
    role: 'assistant',
    text: '',
    sources: [],
    status: webOn.value ? 'web_search' : 'pending',
    error: '',
    thoughts: [], // Agent 思考过程（thought 事件）
    toolCalls: [], // Agent 工具调用卡片（tool_call/tool_result 事件）
    retryable: false,
  }
  messages.value.push(assistant)
  // 必须从响应式数组取代理引用——直接持有原始对象，修改属性不触发视图更新
  currentMsg = messages.value[messages.value.length - 1]
  streaming.value = true
  scrollToBottom(true) // 发送新消息：强制滚动到底部

  controller = new AbortController()
  const history = buildHistory()
  try {
    await chatStream(
      {
        question,
        history,
        top_k: 4,
        conversation_id: convId.value || undefined,
        web_search: webOn.value || undefined, // 开启联网：后端抓最新网页资料进上下文
      },
      (event, data) => {
        const payload = JSON.parse(data)
        // 事件回调里统一从响应式数组取 proxy 引用——sources/status 必须走代理才能触发视图更新
        currentMsg = messages.value[messages.value.length - 1]
        if (event === 'thought') {
          currentMsg.thoughts.push(payload.message)
        } else if (event === 'tool_call') {
          currentMsg.toolCalls.push({
            id: payload.tool_call_id,
            name: payload.name,
            args: payload.args || {},
            status: 'running',
            summary: '',
          })
        } else if (event === 'tool_result') {
          const card = currentMsg.toolCalls.find((c) => c.id === payload.tool_call_id)
          if (card) {
            card.status = payload.ok ? 'done' : 'error'
            card.summary = payload.summary || ''
          }
        } else if (event === 'sources') {
          currentMsg.sources = payload.sources
          currentMsg.status = 'streaming' // 检索完成 → 生成中
          scrollToBottom()
        } else if (event === 'delta') {
          currentMsg.status = 'streaming'
          pushDelta(payload.content)
        } else if (event === 'done') {
          // 保证已收到所有 delta（rAF 可能尚未 flush）
          currentMsg.text = payload.answer
          currentMsg.status = 'done'
          currentMsg.toolTrace = payload.tool_trace || []
          // 收尾仍为 running 的卡片（个别工具可能无 tool_result）
          currentMsg.toolCalls.forEach((c) => { if (c.status === 'running') c.status = 'done' })
          if (rafId) { cancelAnimationFrame(rafId); rafId = null }
          pending = ''
        } else if (event === 'error') {
          throw new Error(payload.message || '生成失败')
        }
      },
      { signal: controller.signal },
    )
  } catch (e) {
    const m = messages.value[messages.value.length - 1]
    if (e.name === 'AbortError') {
      m.status = 'done' // 用户主动停止，保留已出文本
    } else {
      m.status = 'error'
      m.error = e.message || String(e)
      m.retryable = true // 支持重试
    }
  } finally {
    if (rafId) { cancelAnimationFrame(rafId); rafId = null }
    pending = ''
    currentMsg = null
    streaming.value = false
    controller = null
    scrollToBottom()
    if (convId.value) emit('updated') // 落库完成 → 侧边栏刷新标题/顺序
  }
}

function stop() {
  if (controller) controller.abort()
}

// 重试失败的回答：移除"失败回答 + 其用户消息"，重新发送同一问题
async function sendText(question) {
  input.value = question
  await send()
}

async function retry(failedMsg) {
  const idx = messages.value.indexOf(failedMsg)
  if (idx < 0) return
  messages.value.splice(Math.max(0, idx - 1)) // 移除失败回答与对应的用户消息，避免重复
  if (controller) { controller.abort(); controller = null }
  streaming.value = false
  if (lastQuestion.value) await sendText(lastQuestion.value)
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

onBeforeUnmount(() => {
  if (controller) controller.abort()
})
</script>

<template>
  <div class="chat-view">
    <div ref="listEl" class="chat-messages">
      <div class="chat-inner">
        <div v-if="!messages.length" class="welcome">
          <div class="logo-lg">Interview Helper</div>
          <div class="sub">上传你的简历/面经，问任何面试问题 —— 回答带引用溯源</div>
          <div class="sub" style="margin-top:4px">试试：「行为面试怎么准备？」「根据我的简历，我的核心优势是什么？」</div>
        </div>
        <MessageBubble v-for="(m, i) in messages" :key="i" :message="m" @retry="retry(m)" />
      </div>
    </div>

    <div class="chat-input">
      <div class="attach-bar" v-if="attachments.length">
        <div
          v-for="(a, i) in attachments"
          :key="i"
          class="attach-item"
          :class="a.status"
        >
          <span v-if="a.status === 'uploading'">⏳</span>
          <span v-else-if="a.status === 'done'">✅</span>
          <span v-else>❌</span>
          {{ a.name }}
          <span class="attach-msg">{{ a.message }}</span>
        </div>
      </div>
      <div class="web-toggle-row">
        <button
          class="web-toggle"
          :class="{ on: webOn }"
          :disabled="streaming"
          title="联网搜索：抓取最新网页资料（Bing 搜索 + 正文提取，无 API key）"
          @click="webOn = !webOn"
        >🌐 联网搜索</button>
        <span v-if="webOn" class="web-toggle-hint">已开启：回答将结合最新网页资料，首字响应慢 3-7 秒</span>
      </div>
      <div class="input-wrap">
        <div class="input-box">
          <button class="attach-btn" title="上传文件（≤10MB，本对话最多 5 个）" @click="fileInput.click()">＋</button>
          <input
            ref="fileInput"
            type="file"
            multiple
            hidden
            accept=".pdf,.docx,.xlsx,.txt,.jpg,.jpeg,.png,.bmp,.webp"
            @change="onPick"
          />
          <textarea
            v-model="input"
            :disabled="streaming"
            placeholder="输入你的问题…（Enter 发送，Shift+Enter 换行）"
            @keydown="onKeydown"
          ></textarea>
        </div>
        <button v-if="streaming" class="send-btn" style="background:var(--danger);box-shadow:none" title="停止生成" @click="stop">■</button>
        <button v-else class="send-btn" title="发送" @click="send">➤</button>
      </div>
      <div class="hint">
        {{ messages.length ? '回答中的 [1][2] 可点击查看原文引用 · ' : '' }}生成由 DeepSeek 提供 · 引用溯源自知识库
      </div>
      <div class="hint">
        支持格式：PDF / DOCX / XLSX / TXT / JPG / PNG 等（单文件 ≤10MB，每次对话最多 5 个）
      </div>
    </div>
  </div>
</template>