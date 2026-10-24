<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatView from './components/ChatView.vue'
import DocListView from './components/DocListView.vue'
import AgentWorkbench from './components/AgentWorkbench.vue'
import BrandMark from './components/BrandMark.vue'
import { listConversations, deleteConversation } from './api.js'

const tab = ref('chat')
const conversations = ref([]) // 侧边栏会话列表（本地持久化）
const activeId = ref(null) // 当前打开的会话 id（null = 新对话）
const chatKey = ref(0) // 切换会话时让 ChatView 重挂载，从服务端重新加载上下文

// ---- 侧栏：搜索 / 折叠 / 移动端抽屉 ----
const search = ref('')
const sidebarCollapsed = ref(readCollapsed())
const drawerOpen = ref(false)
const mediaQuery = window.matchMedia('(max-width: 900px)')
const isMobile = ref(mediaQuery.matches)
let mediaHandler = null

function readCollapsed() {
  try {
    return localStorage.getItem('ih.sidebar.collapsed') === '1'
  } catch {
    return false // 隐私模式下忽略
  }
}

async function load() {
  try {
    conversations.value = await listConversations()
  } catch (e) {
    console.error('加载会话列表失败', e)
  }
}

// ---- 会话按日期分组（参考 DeepSeek 侧栏） ----
const GROUP_ORDER = ['今天', '昨天', '7 天内', '30 天内', '更早']

function dayStart(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

function groupLabel(item) {
  const raw = item.updated_at || item.created_at
  const d = raw ? new Date(raw) : null
  if (!d || Number.isNaN(d.getTime())) return '更早'
  const diff = Math.round((dayStart(new Date()) - dayStart(d)) / 86400000)
  if (diff <= 0) return '今天'
  if (diff === 1) return '昨天'
  if (diff < 7) return '7 天内'
  if (diff < 30) return '30 天内'
  return '更早'
}

const activeConversation = computed(
  () => conversations.value.find((c) => c.id === activeId.value) || null,
)

const filteredConversations = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return conversations.value
  return conversations.value.filter((c) => (c.title || '').toLowerCase().includes(q))
})

const groupedConversations = computed(() => {
  const buckets = new Map(GROUP_ORDER.map((label) => [label, []]))
  filteredConversations.value.forEach((c) => buckets.get(groupLabel(c)).push(c))
  return GROUP_ORDER.filter((label) => buckets.get(label).length).map((label) => ({
    label,
    items: buckets.get(label),
  }))
})

const pageTitle = computed(() => {
  if (tab.value === 'docs') return '知识库'
  if (tab.value === 'workbench') return 'Agent 工作台'
  return activeConversation.value?.title || '新对话'
})

// ---- 交互 ----
function closeDrawer() {
  drawerOpen.value = false
}

function toggleCollapse() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  try {
    localStorage.setItem('ih.sidebar.collapsed', sidebarCollapsed.value ? '1' : '0')
  } catch {
    /* 隐私模式下忽略 */
  }
}

function switchTab(t) {
  tab.value = t
  closeDrawer()
}

function newChat() {
  activeId.value = null
  chatKey.value++ // 重挂载 → 空对话
  tab.value = 'chat'
  closeDrawer()
}

function openChat(id) {
  // 点的是当前会话：只切回对话 tab，不重挂载 ChatView。
  // 重挂载会触发 onBeforeUnmount → abort 正在进行的 SSE，回答/状态会丢。
  if (id === activeId.value) {
    tab.value = 'chat'
    closeDrawer()
    return
  }
  activeId.value = id
  chatKey.value++ // 重挂载 → 从服务端拉取该会话消息
  tab.value = 'chat'
  closeDrawer()
}

async function removeChat(id) {
  if (!confirm('删除该对话？记录将不可恢复')) return
  try {
    await deleteConversation(id)
    conversations.value = conversations.value.filter((c) => c.id !== id)
    // 删除的是当前会话 → 回到新对话；否则保持当前对话的流式状态不变
    if (activeId.value === id) newChat()
  } catch (err) {
    alert('删除失败：' + err.message)
  }
}

// ChatView 首次发送时新建会话成功 → 记录 activeId（不重挂载，保持流式不断）
function onCreated(id) {
  activeId.value = id
  load()
}

// 消息落库后刷新列表（标题/活跃顺序变化）
function onUpdated() {
  load()
}

function onKeydown(e) {
  if (e.key === 'Escape' && drawerOpen.value) closeDrawer()
}

watch(drawerOpen, (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
})

onMounted(() => {
  load()
  mediaHandler = (e) => {
    isMobile.value = e.matches
    if (!e.matches) closeDrawer()
  }
  mediaQuery.addEventListener('change', mediaHandler)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  if (mediaHandler) mediaQuery.removeEventListener('change', mediaHandler)
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <div class="app-shell">
    <!-- 移动端遮罩 -->
    <div v-if="isMobile && drawerOpen" class="sidebar-scrim" @click="closeDrawer"></div>

    <!-- 侧边栏：品牌 + 新建对话 + 搜索 + 分组会话 + 导航 -->
    <aside
      class="sidebar"
      :class="{ 'is-collapsed': sidebarCollapsed && !isMobile, 'is-open': drawerOpen }"
    >
      <div class="side-brand">
        <BrandMark :size="30" />
        <span class="brand-word">Interview Helper</span>
        <button
          class="side-collapse"
          type="button"
          :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
          :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
          @click="toggleCollapse"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3.5" y="4.5" width="17" height="15" rx="3" />
            <path d="M9.5 4.5v15" />
          </svg>
        </button>
      </div>

      <button class="new-chat" type="button" title="开启新对话" @click="newChat">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
        <span class="new-chat-label">开启新对话</span>
      </button>

      <div class="side-search">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          <circle cx="11" cy="11" r="6.5" />
          <path d="m16 16 4 4" />
        </svg>
        <input v-model="search" type="text" placeholder="搜索对话" aria-label="搜索对话" />
        <button
          v-if="search"
          class="search-clear"
          type="button"
          title="清除搜索"
          aria-label="清除搜索"
          @click="search = ''"
        >×</button>
      </div>

      <div class="conv-scroll">
        <template v-if="groupedConversations.length">
          <div v-for="group in groupedConversations" :key="group.label" class="conv-group">
            <div class="conv-group-label">{{ group.label }}</div>
            <div
              v-for="c in group.items"
              :key="c.id"
              class="conv-item"
              :class="{ 'is-active': c.id === activeId }"
            >
              <button class="conv-open" type="button" @click="openChat(c.id)">
                <span class="conv-title">{{ c.title || '新对话' }}</span>
              </button>
              <button
                class="conv-del"
                type="button"
                title="删除对话"
                aria-label="删除对话"
                @click="removeChat(c.id)"
              >×</button>
            </div>
          </div>
        </template>
        <div v-else class="conv-empty">
          {{ search ? '未找到相关对话' : '暂无对话记录' }}
        </div>
      </div>

      <nav class="side-nav">
        <button
          class="side-nav-item"
          :class="{ active: tab === 'chat' }"
          type="button"
          title="对话"
          @click="switchTab('chat')"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5H9l-5 3.5V6.5Z" />
          </svg>
          <span>对话</span>
        </button>
        <button
          class="side-nav-item"
          :class="{ active: tab === 'docs' }"
          type="button"
          title="知识库"
          @click="switchTab('docs')"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Z" />
            <path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20" />
          </svg>
          <span>知识库</span>
        </button>
        <button
          class="side-nav-item"
          :class="{ active: tab === 'workbench' }"
          type="button"
          title="Agent 工作台"
          @click="switchTab('workbench')"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
            <circle cx="15" cy="7" r="2" />
            <circle cx="9" cy="17" r="2" />
          </svg>
          <span>工作台</span>
        </button>
      </nav>

      <div class="side-foot">
        <span class="status-dot"></span>
        <span class="status-text">服务运行中 · 本地保存</span>
        <span class="side-avatar" title="本地用户">IH</span>
      </div>
    </aside>

    <main class="app-main">
      <!-- 移动端顶栏（桌面端由 ChatView 内的顶栏承担） -->
      <header class="mobile-topbar">
        <button
          class="mobile-menu"
          type="button"
          title="打开菜单"
          aria-label="打开菜单"
          @click="drawerOpen = true"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
        <span class="mobile-title">{{ pageTitle }}</span>
        <span class="model-badge">DeepSeek · Agent</span>
      </header>

      <ChatView
        v-show="tab === 'chat'"
        :key="chatKey"
        :conversation-id="activeId"
        :conversation-title="activeConversation?.title || '新对话'"
        @created="onCreated"
        @updated="onUpdated"
      />
      <DocListView v-show="tab === 'docs'" />
      <AgentWorkbench v-show="tab === 'workbench'" />
    </main>
  </div>
</template>
