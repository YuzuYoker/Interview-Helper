<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { chatStream, uploadDocument, getUploadStatus, createConversation, getConversation } from '../api.js'
import MessageBubble from './MessageBubble.vue'
import BrandMark from './BrandMark.vue'

const props = defineProps({
  conversationId: { type: String, default: null }, // App 传入：当前会话（null = 新对话）
  conversationTitle: { type: String, default: '新对话' },
})
const emit = defineEmits(['created', 'updated'])

const messages = ref([])
const input = ref('')
const streaming = ref(false)
const listEl = ref(null)
const textareaEl = ref(null)
const fileInput = ref(null)
const attachments = ref([]) // { name, status: 'uploading'|'done'|'err', message }
const uploadedCount = ref(0) // 本次对话已提交文件数（上限 5）
const convId = ref(props.conversationId)
const webOn = ref(false) // 「联网搜索」开关（Agent 模式下仅作系统提示 hint，是否联网由 LLM 决策）
const lastQuestion = ref('') // 重试用：记住最近一次问题
let controller = null
let uidSeq = 0

const nextUid = () => ++uidSeq

// ---- 上传限制：每对话 ≤5 个文件，单个 ≤10MB ----
const MAX_FILES_PER_CHAT = 5
const MAX_FILE_SIZE = 10 * 1024 * 1024
const UPLOAD_TIP = '支持 PDF / DOCX / XLSX / TXT / 图片（单文件 ≤10MB，每次对话最多 5 个）'

// ---- 欢迎页建议（参考 DeepSeek 首页卡片） ----
const SUGGESTIONS = [
  {
    title: '简历匹配分析',
    desc: '分析我的简历与目标岗位的匹配度',
    paths: ['M6 3.5h8.5L19 8v12.5H6z', 'M14 3.5V8h5', 'M9 13l2 2 3.5-3.5'],
  },
  {
    title: '行为面试准备',
    desc: '帮我准备行为面试的高频问题',
    paths: ['M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5H9l-5 3.5V6.5Z'],
  },
  {
    title: '模拟技术面试',
    desc: '围绕目标岗位进行一场模拟面试',
    paths: ['M8.5 8 5 12l3.5 4', 'M15.5 8 19 12l-3.5 4', 'M13.5 5.5 10.5 18.5'],
  },
  {
    title: '面试冲刺计划',
    desc: '制定一份两周面试冲刺计划',
    paths: ['M4.5 6.5A1.5 1.5 0 0 1 6 5h12a1.5 1.5 0 0 1 1.5 1.5V19H6a1.5 1.5 0 0 1-1.5-1.5z', 'M8 3.5V7M16 3.5V7M4.5 10h15'],
  },
]

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
        uid: nextUid(),
        role: m.role,
        text: m.content,
        sources: m.sources || [],
        status: 'done', // 历史消息都是已完成状态，不再显示生成胶囊
        error: '',
        thoughts: m.thoughts || [], // 后端已持久化思考过程，刷新后可展开
        toolCalls,
        retryable: false,
      }
    })
  } catch (e) {
    console.error('加载会话失败', e)
  }
}

// ---- 输入框自适应高度（field-sizing 的兼容实现） ----
function autoGrow() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

watch(input, () => nextTick(autoGrow))

// ---- 对话内上传（+ 按钮）----
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

// ---- 打字机：rAF 节流，每帧至多一次 DOM 更新 ----
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
    .filter((m) => (m.role === 'assistant' ? m.status === 'done' : true))
    .slice(-8)
    .map((m) => ({ role: m.role, content: m.text }))
}

const lastAssistantIndex = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'assistant') return i
  }
  return -1
})

// 执行一轮问答：send / 建议卡片 / 重试 / 重新生成共用
async function runTurn(question, { appendUser = true, regenerate = false } = {}) {
  if (!question || streaming.value) return
  lastQuestion.value = question

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

  if (appendUser) {
    messages.value.push({
      uid: nextUid(),
      role: 'user',
      text: question,
      sources: [],
      status: 'done',
    })
  }
  // 开关开启时先显示"正在联网搜索…"；收到 sources 事件后转为"正在生成回答…"
  messages.value.push({
    uid: nextUid(),
    role: 'assistant',
    text: '',
    sources: [],
    status: webOn.value ? 'web_search' : 'pending',
    error: '',
    thoughts: [], // Agent 思考过程（thought 事件）
    toolCalls: [], // Agent 工具调用卡片（tool_call/tool_result 事件）
    retryable: false,
  })
  // 必须从响应式数组取代理引用——直接持有原始对象，修改属性不触发视图更新
  const assistant = messages.value[messages.value.length - 1]
  currentMsg = assistant
  streaming.value = true
  scrollToBottom(true) // 发送新消息：强制滚动到底部

  const runController = new AbortController()
  controller = runController
  const history = buildHistory()
  try {
    await chatStream(
      {
        question,
        history,
        top_k: 4,
        conversation_id: convId.value || undefined,
        web_search: webOn.value || undefined, // 开启联网：后端抓最新网页资料进上下文
        regenerate: regenerate || undefined, // 重新生成/重试：服务端截断旧回答并复用 user 消息
      },
      (event, data) => {
        const payload = JSON.parse(data)
        // 本轮回调只更新本轮创建的助手消息，避免重试/重新生成时串到新消息
        currentMsg = assistant
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
      { signal: runController.signal },
    )
  } catch (e) {
    if (e.name === 'AbortError') {
      assistant.status = 'done' // 用户主动停止，保留已出文本
    } else {
      assistant.status = 'error'
      assistant.error = e.message || String(e)
      assistant.retryable = true // 支持重试
    }
  } finally {
    // 只有本次请求仍是“当前请求”时才重置共享状态。
    // 重试/重新生成会 abort 后立刻启动新请求，旧请求的收尾不能覆盖新请求的状态。
    if (controller === runController) {
      if (rafId) { cancelAnimationFrame(rafId); rafId = null }
      pending = ''
      currentMsg = null
      streaming.value = false
      controller = null
      // 流意外结束（无 done / error 事件）时不要让消息永久停在“检索中”
      if (assistant.status !== 'done' && assistant.status !== 'error') {
        assistant.status = 'error'
        assistant.error = '连接中断，未收到回答，请重试'
        assistant.retryable = true
      }
    }
    scrollToBottom()
    if (convId.value) emit('updated') // 落库完成 → 侧边栏刷新标题/顺序
  }
}

async function send() {
  const question = input.value.trim()
  if (!question || streaming.value) return
  input.value = ''
  await runTurn(question, { appendUser: true })
}

async function sendSuggestion(text) {
  if (streaming.value) return
  input.value = text
  await send()
}

function stop() {
  if (controller) controller.abort()
}

// 重试失败的回答：移除"失败回答 + 其用户消息"，重新发送同一问题。
// regenerate=true 让服务端复用已落库的 user 消息，避免刷新后出现重复轮次。
async function retry(failedMsg) {
  const idx = messages.value.indexOf(failedMsg)
  if (idx < 0) return
  messages.value.splice(Math.max(0, idx - 1)) // 移除失败回答与对应的用户消息，避免重复
  if (controller) { controller.abort(); controller = null }
  streaming.value = false
  if (lastQuestion.value) {
    await runTurn(lastQuestion.value, { appendUser: true, regenerate: true })
  }
}

// 重新生成：删除最后一条回答，用同一问题再跑一轮（不新增用户气泡）
async function regenerate() {
  if (streaming.value) return
  let lastUser = messages.value.length - 1
  while (lastUser >= 0 && messages.value[lastUser].role !== 'user') lastUser--
  if (lastUser < 0) return
  const question = messages.value[lastUser].text
  messages.value.splice(lastUser + 1)
  await runTurn(question, { appendUser: false, regenerate: true })
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

onMounted(async () => {
  await loadConversation()
  await nextTick()
  autoGrow()
  if (messages.value.length) scrollToBottom(true)
})

onBeforeUnmount(() => {
  if (controller) controller.abort()
})
</script>

<template>
  <div class="chat-view">
    <header class="chat-topbar">
      <div class="chat-topbar-inner">
        <div class="chat-title">{{ conversationTitle }}</div>
        <span class="model-badge">DeepSeek · Agent</span>
      </div>
    </header>

    <div ref="listEl" class="chat-scroll" :class="{ 'is-empty': !messages.length }">
      <div class="chat-inner">
        <div v-if="!messages.length" class="welcome">
          <BrandMark :size="56" class="welcome-mark" />
          <h1>我是 Interview Helper，很高兴见到你！</h1>
          <p>上传简历、JD 或面经，我会结合你的资料给出带引用溯源的面试建议。</p>
          <div class="suggest-grid">
            <button
              v-for="s in SUGGESTIONS"
              :key="s.title"
              class="suggest-card"
              type="button"
              :disabled="streaming"
              @click="sendSuggestion(s.desc)"
            >
              <span class="suggest-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                  <path v-for="(d, i) in s.paths" :key="i" :d="d" />
                </svg>
              </span>
              <span class="suggest-text">
                <strong>{{ s.title }}</strong>
                <small>{{ s.desc }}</small>
              </span>
            </button>
          </div>
        </div>

        <MessageBubble
          v-for="(m, i) in messages"
          :key="m.uid"
          :message="m"
          :can-regenerate="i === lastAssistantIndex && !streaming"
          @retry="retry(m)"
          @regenerate="regenerate"
        />
      </div>
    </div>

    <footer class="composer-wrap">
      <div class="composer">
        <div v-if="attachments.length" class="composer-attachments">
          <div
            v-for="(a, i) in attachments"
            :key="i"
            class="attach-chip"
            :class="a.status"
          >
            <span class="attach-icon">
              <span v-if="a.status === 'uploading'" class="mini-spinner"></span>
              <template v-else-if="a.status === 'done'">✓</template>
              <template v-else>!</template>
            </span>
            <span class="attach-name">{{ a.name }}</span>
            <span class="attach-msg">{{ a.message }}</span>
          </div>
        </div>

        <div class="composer-box">
          <textarea
            ref="textareaEl"
            v-model="input"
            :disabled="streaming"
            rows="1"
            placeholder="给 Interview Helper 发送消息…"
            @keydown="onKeydown"
          ></textarea>

          <div class="composer-toolbar">
            <button
              class="tool-btn"
              type="button"
              :title="UPLOAD_TIP"
              aria-label="上传文件"
              @click="fileInput.click()"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
                <path d="M12 5.5v13M5.5 12h13" />
              </svg>
            </button>
            <input
              ref="fileInput"
              type="file"
              multiple
              hidden
              accept=".pdf,.docx,.xlsx,.txt,.jpg,.jpeg,.png,.gif,.webp"
              @change="onPick"
            />

            <button
              class="web-pill"
              :class="{ on: webOn }"
              type="button"
              :disabled="streaming"
              title="联网搜索：抓取最新网页资料（Bing 搜索 + 正文提取）"
              @click="webOn = !webOn"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
                <circle cx="12" cy="12" r="8.5" />
                <path d="M3.5 12h17M12 3.5c2.5 2.3 3.8 5.3 3.8 8.5S14.5 18.2 12 20.5C9.5 18.2 8.2 15.2 8.2 12S9.5 5.8 12 3.5Z" />
              </svg>
              <span>联网搜索</span>
            </button>

            <span class="toolbar-spacer"></span>

            <button
              v-if="streaming"
              class="send-btn stop"
              type="button"
              title="停止生成"
              aria-label="停止生成"
              @click="stop"
            >
              <svg viewBox="0 0 24 24" fill="none">
                <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
              </svg>
            </button>
            <button
              v-else
              class="send-btn"
              :class="{ ready: input.trim() }"
              type="button"
              :disabled="!input.trim()"
              title="发送"
              aria-label="发送"
              @click="send"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 19V5M6 11l6-6 6 6" />
              </svg>
            </button>
          </div>
        </div>
      </div>
      <p class="composer-disclaimer">
        内容由 AI 生成，请仔细甄别{{ messages.length ? ' · 回答中的 [1][2] 可点击查看引用' : '' }}
      </p>
    </footer>
  </div>
</template>
