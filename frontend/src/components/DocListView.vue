<script setup>
import { ref, onMounted } from 'vue'
import { listDocuments, uploadDocument, getUploadStatus, deleteDocument } from '../api.js'

const docs = ref([])
const loading = ref(false)
const dragover = ref(false)
const uploads = ref([]) // { name, status: 'processing'|'ok'|'err', message }
const fileInput = ref(null)

async function refresh() {
  loading.value = true
  try {
    const r = await listDocuments()
    docs.value = r.documents
  } catch (e) {
    uploads.value.push({ name: '加载列表失败', status: 'err', message: e.message })
  } finally {
    loading.value = false
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// 异步上传：提交后每 2s 轮询后台任务状态（串行提交，后台单 worker 处理）
async function uploadAll(files) {
  for (const file of files) {
    const item = { name: file.name, status: 'processing', message: '后台向量化中…' }
    uploads.value.push(item)
    try {
      const r = await uploadDocument(file)
      // 轮询状态直到 done / error
      for (let i = 0; i < 300; i++) {
        await sleep(2000)
        const st = await getUploadStatus(r.document_id)
        if (st.status === 'done') {
          item.status = 'ok'
          item.message = `${st.chunk_count} 个分块已入库`
          break
        }
        if (st.status === 'error') {
          item.status = 'err'
          item.message = st.error || '处理失败'
          break
        }
      }
    } catch (e) {
      if (e.message.includes('429')) {
        item.status = 'err'
        item.message = '上传过于频繁，请稍后再试'
      } else {
        item.status = 'err'
        item.message = e.message || String(e)
      }
    }
  }
  uploads.value = uploads.value.filter((u) => u.status !== 'ok') // 成功项稍后清掉
  await refresh()
  setTimeout(() => { uploads.value = [] }, 4000)
}

function onDrop(e) {
  dragover.value = false
  const files = [...e.dataTransfer.files]
  if (files.length) uploadAll(files)
}

function onPick(e) {
  uploadAll([...e.target.files])
  e.target.value = ''
}

async function onDelete(doc) {
  if (!confirm(`删除文档「${doc.filename}」？`)) return
  try {
    await deleteDocument(doc.document_id)
    await refresh()
  } catch (e) {
    alert(e.message.includes('409') ? '文档正在处理中，请稍后再试' : e.message)
  }
}

function formatTime(v) {
  if (!v) return ''
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

onMounted(refresh)
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div class="page-heading">
        <div class="page-eyebrow">KNOWLEDGE BASE</div>
        <h1 class="page-title">知识库</h1>
        <p class="page-subtitle">上传简历、面经与岗位资料，让回答有据可依</p>
      </div>
      <div class="page-actions">
        <span class="count-badge">{{ docs.length }} 个文档</span>
        <button class="btn btn-secondary" type="button" :disabled="loading" @click="refresh">
          刷新
        </button>
      </div>
    </header>

    <div
      class="drop-zone"
      :class="{ dragover }"
      role="button"
      tabindex="0"
      @click="fileInput.click()"
      @keydown.enter.prevent="fileInput.click()"
      @keydown.space.prevent="fileInput.click()"
      @dragover.prevent="dragover = true"
      @dragleave="dragover = false"
      @drop.prevent="onDrop"
    >
      <div class="drop-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M6 3.5h8L19 8.5v12H6z" />
          <path d="M14 3.5v5h5" />
          <path d="M12 17v-5M9.5 14.5 12 12l2.5 2.5" />
        </svg>
      </div>
      <strong>拖拽文件到此处，或点击选择文件</strong>
      <div class="drop-hint">支持 PDF / Word / Excel / TXT / 图片（图片由 DeepSeek 多模态识别）</div>
      <div class="drop-hint muted">不支持 .doc 旧格式 · 单文件建议 ≤10MB</div>
      <input ref="fileInput" type="file" multiple hidden @change="onPick" />
    </div>

    <div v-if="uploads.length" class="upload-list">
      <div v-for="(u, i) in uploads" :key="i" class="upload-row" :class="u.status">
        <span class="upload-icon">
          <span v-if="u.status === 'processing'" class="mini-spinner"></span>
          <template v-else-if="u.status === 'ok'">✓</template>
          <template v-else>!</template>
        </span>
        <span class="upload-name">{{ u.name }}</span>
        <span class="upload-msg">{{ u.message }}</span>
      </div>
    </div>

    <section class="doc-section">
      <div class="section-head">
        <h2>全部文档</h2>
        <span class="section-count">{{ docs.length }}</span>
      </div>

      <div v-if="loading" class="page-empty">加载中…</div>
      <div v-else-if="!docs.length" class="page-empty">
        <div class="empty-icon">📄</div>
        <p>知识库为空，上传你的面试资料开始</p>
      </div>
      <div v-else class="doc-list">
        <div v-for="doc in docs" :key="doc.document_id" class="doc-row">
          <span class="doc-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
              <path d="M6 3.5h8L19 8.5v12H6z" />
              <path d="M14 3.5v5h5" />
            </svg>
          </span>
          <div class="doc-info">
            <div class="doc-name">{{ doc.filename }}</div>
            <div class="doc-meta">
              {{ doc.status === 'indexed' ? doc.chunk_count + ' 个分块' : '后台任务' }}
              <template v-if="formatTime(doc.created_at)"> · {{ formatTime(doc.created_at) }}</template>
            </div>
          </div>
          <span v-if="doc.status === 'processing'" class="badge warning">处理中</span>
          <span v-else-if="doc.status === 'error'" class="badge danger">处理失败</span>
          <span v-else class="badge success">已索引</span>
          <button
            class="text-btn danger"
            type="button"
            :disabled="doc.status === 'processing'"
            title="删除文档"
            @click="onDelete(doc)"
          >删除</button>
        </div>
      </div>
    </section>
  </section>
</template>
