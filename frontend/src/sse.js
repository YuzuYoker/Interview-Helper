// SSE over fetch：POST + ReadableStream 手写解析（EventSource 不支持 POST）
export async function fetchSSE(url, body, { onEvent, signal } = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    const msg = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}${msg ? ': ' + msg : ''}`)
  }

  const reader = res.body.getReader()
  // stream:true 关键：中文 3 字节可能被 TCP 块切断
  const decoder = new TextDecoder('utf-8', { stream: true })
  let buf = ''
  let event = 'message'
  let dataLines = []

  const flush = () => {
    if (!dataLines.length) return
    const data = dataLines.join('\n')
    dataLines = []
    onEvent(event, data)
    event = 'message'
  }

  const parseBlock = (block) => {
    for (const line of block.split('\n')) {
      const t = line.trim()
      if (!t || t.startsWith(':')) continue
      if (t.startsWith('event:')) event = t.slice(6).trim()
      else if (t.startsWith('data:')) dataLines.push(t.slice(5).trimStart())
    }
    flush()
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // 事件以空行 \n\n 分隔，可能跨多个 TCP 块
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      parseBlock(block)
    }
  }
  // 收尾：处理最后一段无空行的数据
  if (buf.trim()) parseBlock(buf)
}
