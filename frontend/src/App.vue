<script setup>
import { ref, onMounted } from 'vue'
import ChatView from './components/ChatView.vue'
import DocListView from './components/DocListView.vue'
import AgentWorkbench from './components/AgentWorkbench.vue'
import { listConversations, deleteConversation } from './api.js'

const tab = ref('chat')
const conversations = ref([]) // 侧边栏会话列表（本地持久化）
const activeId = ref(null) // 当前打开的会话 id（null = 新对话）
const chatKey = ref(0) // 切换会话时让 ChatView 重挂载，从服务端重新加载上下文

async function load() {
  try {
    conversations.value = await listConversations()
  } catch (e) {
    console.error('加载会话列表失败', e)
  }
}

function newChat() {
  activeId.value = null
  chatKey.value++ // 重挂载 → 空对话
  tab.value = 'chat'
}

function openChat(id) {
  activeId.value = id
  chatKey.value++ // 重挂载 → 从服务端拉取该会话消息
  tab.value = 'chat'
}

async function removeChat(id, e) {
  e.stopPropagation()
  if (!confirm('删除该对话？记录将不可恢复')) return
  try {
    await deleteConversation(id)
    conversations.value = conversations.value.filter((c) => c.id !== id)
    if (activeId.value === id) newChat()
  } catch (err) {
    alert('删除失败：' + err.message)
  }
}

// ChatView 首次发送时新建会话成功 → 记录 activeId（不重挂载，保持流式不断）
function onCreated(id) {
  activeId.value = id
}

// 消息落库后刷新列表（标题/活跃顺序变化）
function onUpdated() {
  load()
}

onMounted(load)
</script>

<template>
  <div class="app-shell">
    <!-- 侧边栏：品牌 + 新建对话 + 会话列表 + tab 切换（参考 DeepSeek 官网） -->
    <aside class="sidebar">
      <div class="brand">Interview Helper</div>
      <button class="new-chat-btn" @click="newChat">＋ 新建对话</button>

      <div class="conv-list">
        <div
          v-for="c in conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === activeId }"
          @click="openChat(c.id)"
        >
          <span class="conv-title">{{ c.title }}</span>
          <span class="conv-del" title="删除对话" @click="removeChat(c.id, $event)">×</span>
        </div>
        <div v-if="!conversations.length" class="conv-empty">暂无对话记录</div>
      </div>

      <nav class="nav-tabs">
        <button :class="{ active: tab === 'chat' }" @click="tab = 'chat'">💬 对话</button>
        <button :class="{ active: tab === 'docs' }" @click="tab = 'docs'">📚 知识库</button>
        <button :class="{ active: tab === 'workbench' }" @click="tab = 'workbench'">🛠 工作台</button>
      </nav>
      <div class="side-foot">
        <span class="dot"></span>服务运行中 · 对话保留本地
      </div>
    </aside>

    <main class="app-main">
      <ChatView
        v-show="tab === 'chat'"
        :key="chatKey"
        :conversation-id="activeId"
        @created="onCreated"
        @updated="onUpdated"
      />
      <DocListView v-show="tab === 'docs'" />
      <AgentWorkbench v-show="tab === 'workbench'" />
    </main>
  </div>
</template>
