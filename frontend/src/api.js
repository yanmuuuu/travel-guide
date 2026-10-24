import { marked } from 'marked'
import DOMPurify from 'dompurify'

const BASE = import.meta.env.VITE_API_BASE || '/api'

let onUnauthorized = () => {}
export const setUnauthorizedHandler = (fn) => (onUnauthorized = fn)

export const getToken = () => localStorage.getItem('token')
export const setToken = (t) => (t ? localStorage.setItem('token', t) : localStorage.removeItem('token'))

function headers() {
  const h = { 'Content-Type': 'application/json' }
  const t = getToken()
  if (t) h.Authorization = `Bearer ${t}`
  return h
}

async function fail(res) {
  if (res.status === 401) onUnauthorized()
  let msg = `${res.status}`
  try {
    msg = (await res.json()).detail || msg
  } catch {}
  throw new Error(msg)
}

async function json(path, options = {}) {
  const res = await fetch(BASE + path, { headers: headers(), ...options })
  if (!res.ok) await fail(res)
  return res.json()
}

export const health = () => json('/health')

export const login = (username, password) =>
  json('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })

export const register = (username, password) =>
  json('/auth/register', { method: 'POST', body: JSON.stringify({ username, password }) })

export const me = () => json('/auth/me')

export const getPreferences = () => json('/preferences')

export const savePreferences = (data) => json('/preferences', { method: 'PUT', body: JSON.stringify(data) })

export const runGuide = (body) => json('/guide', { method: 'POST', body: JSON.stringify(body) })

export const listGuides = () => json('/guides')

export const getGuide = (id) => json(`/guides/${id}`)

// /deep/stream 是 POST 的 SSE，EventSource 只支持 GET，所以手动读流。
export async function deepStream(body, onEvent, signal) {
  const res = await fetch(BASE + '/deep/stream', {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) await fail(res)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const data = raw
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trimStart())
        .join('\n')
      if (data) onEvent(JSON.parse(data))
    }
  }
}

export function renderMarkdown(text) {
  const html = DOMPurify.sanitize(marked.parse(text || ''), { ADD_ATTR: ['target'] })
  return html.replace(/<a /g, '<a target="_blank" rel="noopener" ')
}
