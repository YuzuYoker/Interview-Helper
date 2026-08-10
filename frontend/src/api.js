// 后端 API 封装：REST + SSE 流式
import { fetchSSE } from './sse.js'

const BASE = '/api'

export async function health() {
  return (await fetch(`${BASE}/health`)).json()
}

export async function chat(req) {
  const r = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
  return r.json()
}

// SSE 流式对话：onEvent(event, data) 收到 sources/delta/done/error
export function chatStream(req, onEvent, { signal } = {}) {
  return fetchSSE(`${BASE}/chat/stream`, req, { onEvent, signal })
}

export async function uploadDocument(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${BASE}/documents/upload`, { method: 'POST', body: fd })
  if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
  return r.json() // { document_id, status: "processing" }
}

// 后台任务状态轮询
export async function getUploadStatus(docId) {
  const r = await fetch(`${BASE}/documents/${docId}/status`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() // { status: "processing"|"done"|"error", chunk_count, error }
}

export async function listDocuments() {
  const r = await fetch(`${BASE}/documents`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

export async function deleteDocument(docId) {
  const r = await fetch(`${BASE}/documents/${docId}`, { method: 'DELETE' })
  if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
  return r.json()
}

// ---- 会话（对话记录本地持久化） ----
export async function listConversations() {
  const r = await fetch(`${BASE}/conversations`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

export async function createConversation() {
  const r = await fetch(`${BASE}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() // { id, title, ... }
}

export async function getConversation(id) {
  const r = await fetch(`${BASE}/conversations/${id}`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() // { id, title, messages: [{ role, content, sources, ts }] }
}

export async function deleteConversation(id) {
  const r = await fetch(`${BASE}/conversations/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}
