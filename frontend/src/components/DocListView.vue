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

onMounted(refresh)
</script>

<template>
  <div class="doc-view">
    <div
      class="drop-zone"
      :class="{ dragover }"
      @click="fileInput.click()"
      @dragover.prevent="dragover = true"
      @dragleave="dragover = false"
      @drop.prevent="onDrop"
    >
      <div class="big">📄</div>
      <div>拖拽文件到此处，或点击选择文件</div>
      <div class="sub">支持 PDF / Word / Excel / txt / 图片（图片自动走 Qwen-VL 文字识别）</div>
      <div class="formats">不支持 .doc 旧格式 · 图片建议 &lt;10MB</div>
      <input ref="fileInput" type="file" multiple hidden @change="onPick" />
    </div>

    <div class="upload-status">
      <div v-for="(u, i) in uploads" :key="i" class="item" :class="u.status">
        <span v-if="u.status === 'uploading'">⏳</span>
        <span v-else-if="u.status === 'ok'">✅</span>
        <span v-else>❌</span>
        {{ u.name }} <span style="opacity:.7">{{ u.message }}</span>
      </div>
    </div>

    <div class="doc-list">
      <h3>知识库文档（{{ docs.length }}）</h3>
      <div v-if="loading" style="color:var(--text-dim);font-size:13px">加载中…</div>
      <div v-else-if="!docs.length" class="doc-empty">知识库为空，上传你的面试资料开始</div>
      <div v-for="doc in docs" :key="doc.document_id" class="doc-item">
        <span class="icon">📚</span>
        <div class="info">
          <div class="name">
            {{ doc.filename }}
            <span v-if="doc.status === 'processing'" style="color:var(--yellow);font-size:12px">⏳ 处理中</span>
            <span v-else-if="doc.status === 'error'" style="color:var(--danger);font-size:12px">⚠ 处理失败</span>
          </div>
          <div class="meta">{{ doc.status === 'indexed' ? doc.chunk_count + ' 个分块' : '后台任务' }} · {{ new Date(doc.created_at).toLocaleString() }}</div>
        </div>
        <button class="del-btn" :disabled="doc.status === 'processing'" @click="onDelete(doc)">删除</button>
      </div>
    </div>
  </div>
</template>
