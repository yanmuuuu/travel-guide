<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { deepStream, getToken, health, me, renderMarkdown, runGuide, setToken, setUnauthorizedHandler } from './api'
import AuthPage from './components/AuthPage.vue'
import HistoryPanel from './components/HistoryPanel.vue'
import PreferenceDialog from './components/PreferenceDialog.vue'
import ThinkingTrace from './components/ThinkingTrace.vue'

const newThreadId = () => 'thread-' + Math.random().toString(36).slice(2, 10)

const threadId = ref(localStorage.getItem('threadId') || newThreadId())
const user = ref(null)
const checking = ref(!!getToken())
const deep = ref(false)
const input = ref('')
const busy = ref(false)
const online = ref(null)
const messages = ref([])
const pending = ref(null) // 深度思考停下来问人时的 {question, options}
const steps = ref([])
const startedAt = ref(0)
const elapsed = ref(0)
const listEl = ref(null)
const historyRef = ref(null)
const showSettings = ref(false)
let controller = null
let timer = null

localStorage.setItem('threadId', threadId.value)

setUnauthorizedHandler(logout)

onMounted(async () => {
  health()
    .then((r) => (online.value = r.ok))
    .catch(() => (online.value = false))
  if (getToken()) {
    try {
      user.value = await me()
    } catch {
      setToken(null)
    } finally {
      checking.value = false
    }
  }
})

function onLogin(session) {
  setToken(session.token)
  user.value = session.user
  newChat()
}

function logout() {
  controller?.abort()
  setToken(null)
  user.value = null
  showSettings.value = false
  newChat()
}

function scrollDown() {
  nextTick(() => listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: 'smooth' }))
}

function push(role, content, extra = {}) {
  messages.value.push({ role, content, ...extra })
  scrollDown()
}

function startBusy() {
  busy.value = true
  startedAt.value = Date.now()
  elapsed.value = 0
  timer = setInterval(() => (elapsed.value = Math.floor((Date.now() - startedAt.value) / 1000)), 1000)
  scrollDown()
}

function endBusy() {
  busy.value = false
  clearInterval(timer)
}

function newChat() {
  controller?.abort()
  threadId.value = newThreadId()
  localStorage.setItem('threadId', threadId.value)
  messages.value = []
  pending.value = null
  steps.value = []
}

function submit() {
  pending.value ? answer() : send()
}

async function send(preset) {
  const text = (preset ?? input.value).trim()
  if (!text || busy.value) return
  input.value = ''
  push('user', text)
  if (deep.value) await runDeep({ message: text })
  else await runFast(text)
}

async function runFast(text) {
  startBusy()
  try {
    const r = await runGuide({ text, thread_id: threadId.value })
    push('assistant', r.guide || '（没有生成攻略）', {
      card: { origin: r.origin, destination: r.destination, days: r.days, weather: r.weather },
      seconds: elapsed.value,
    })
  } catch (e) {
    push('error', String(e.message || e))
  } finally {
    endBusy()
  }
}

async function runDeep(body) {
  startBusy()
  pending.value = null
  steps.value = []
  controller = new AbortController()
  try {
    await deepStream(
      { thread_id: threadId.value, ...body },
      (ev) => {
        if (ev.type === 'thought') {
          steps.value.push({ kind: 'thought', agent: ev.agent, text: ev.text })
          scrollDown()
        } else if (ev.type === 'tool_call') {
          steps.value.push({ kind: 'call', agent: ev.agent, id: ev.id, name: ev.name, args: ev.args })
          scrollDown()
        } else if (ev.type === 'tool_result') {
          const call = steps.value.findLast((s) => s.kind === 'call' && s.id === ev.id)
          if (call) {
            call.result = ev.content
            call.error = ev.error
          }
        } else if (ev.type === 'interrupt') {
          const v = typeof ev.value === 'object' && ev.value ? ev.value : { question: String(ev.value), options: [] }
          pending.value = { question: v.question || '请选择', options: v.options || [] }
          push('ask', pending.value.question, { steps: [...steps.value] })
        } else if (ev.type === 'done') {
          push('assistant', ev.text || '（没有生成内容）', {
            guideId: ev.guide_id,
            steps: [...steps.value],
            seconds: elapsed.value,
          })
          if (ev.guide_id) historyRef.value?.refresh()
        }
      },
      controller.signal,
    )
  } catch (e) {
    if (e.name !== 'AbortError') push('error', String(e.message || e))
  } finally {
    endBusy()
    controller = null
  }
}

async function answer(choice) {
  const text = (choice ?? input.value).trim()
  if (!text || busy.value) return
  input.value = ''
  push('user', text)
  await runDeep({ resume: text })
}

function stop() {
  controller?.abort()
}

function openSaved(g) {
  push('assistant', g.content || '', { title: g.title, guideId: g.id })
}

async function copy(text) {
  await navigator.clipboard.writeText(text)
}
</script>

<template>
  <div v-if="checking" class="auth"></div>
  <AuthPage v-else-if="!user" @done="onLogin" />
  <div v-else class="layout">
    <aside class="sidebar">
      <div class="brand">
        <span class="logo">◎</span>
        <span>Travel Guide</span>
      </div>

      <button class="nav-btn" @click="newChat">
        <span class="icon">✎</span> 新对话
      </button>

      <HistoryPanel ref="historyRef" @open="openSaved" />

      <div class="sidebar-foot">
        <button class="nav-btn" @click="showSettings = true">
          <span class="icon">♡</span> 我的偏好
        </button>
        <div class="account">
          <span class="avatar">{{ user.username.slice(0, 1).toUpperCase() }}</span>
          <span class="account-name">{{ user.username }}</span>
          <button class="ghost" @click="logout">退出</button>
        </div>
        <div class="status">
          <span :class="['led', online === null ? '' : online ? 'on' : 'off']"></span>
          {{ online === null ? '连接中' : online ? '后端在线' : '后端离线' }}
        </div>
      </div>
    </aside>

    <main :class="['main', { home: !messages.length && !busy }]">
      <div ref="listEl" class="scroll">
        <div class="column">
          <h1 v-if="!messages.length && !busy" class="hero">最近有什么旅游计划？</h1>

          <template v-for="(m, i) in messages" :key="i">
            <div v-if="m.role === 'user'" class="turn user">
              <div class="user-bubble">{{ m.content }}</div>
            </div>

            <div v-else-if="m.role === 'error'" class="turn">
              <div class="notice error">✕ {{ m.content }}</div>
            </div>

            <div v-else class="turn assistant">
              <details v-if="m.steps && m.steps.length" class="log">
                <summary>
                  思考过程 · 调用 {{ m.steps.filter((s) => s.kind === 'call').length }} 次工具<span v-if="m.seconds">，用时 {{ m.seconds }}s</span>
                </summary>
                <ThinkingTrace :steps="m.steps" />
              </details>

              <div v-if="m.role === 'ask'" class="notice ask">
                <div class="ask-label">需要你确认</div>
                {{ m.content }}
              </div>

              <template v-else>
                <div v-if="m.title" class="doc-title">{{ m.title }}</div>
                <div v-if="m.card && m.card.destination" class="chips">
                  <span class="chip" v-if="m.card.origin">{{ m.card.origin }} → {{ m.card.destination }}</span>
                  <span class="chip" v-else>{{ m.card.destination }}</span>
                  <span class="chip" v-if="m.card.days">{{ m.card.days }} 天</span>
                  <span class="chip muted" v-if="m.card.weather" :title="m.card.weather">☁ {{ m.card.weather.split('\n')[0] }}</span>
                </div>
                <div class="md" v-html="renderMarkdown(m.content)"></div>
                <div class="actions">
                  <button class="ghost" @click="copy(m.content)">复制</button>
                  <span v-if="m.guideId" class="saved">已保存 #{{ m.guideId }}</span>
                  <span v-if="m.seconds && !m.steps" class="saved">{{ m.seconds }}s</span>
                </div>
              </template>
            </div>
          </template>

          <div v-if="busy" class="turn assistant">
            <div class="working">
              <span class="spinner"></span>
              <span class="shimmer">{{ deep ? '深度思考中' : '正在规划行程' }}</span>
              <span class="timer">{{ elapsed }}s</span>
            </div>
            <ThinkingTrace v-if="steps.length" :steps="steps" live />
            <div v-else-if="deep" class="trace-empty">主代理正在拆解任务…</div>
          </div>
        </div>
      </div>

      <div class="dock">
        <div class="column">
          <div v-if="pending && !busy && pending.options.length" class="options">
            <button v-for="o in pending.options" :key="o" @click="answer(o)">{{ o }}</button>
          </div>

          <form class="composer" @submit.prevent="submit">
            <span class="plus">＋</span>
            <textarea
              v-model="input"
              rows="1"
              :placeholder="pending ? '选一个选项，或者直接写你的回答' : '说说你想去哪儿、玩几天'"
              @keydown.enter.exact.prevent="submit"
            ></textarea>
            <button
              type="button"
              :class="['think', { active: deep }]"
              :disabled="busy || !!pending"
              :title="deep ? '多智能体实时查询，可能需要几分钟' : '打开后实时查车票、酒店和餐馆'"
              @click="deep = !deep"
            >
              ◐ 深度思考
            </button>
            <button v-if="busy && deep" type="button" class="send stop" @click="stop" title="停止">■</button>
            <button v-else class="send" type="submit" :disabled="busy || !input.trim()" title="发送">↑</button>
          </form>
        </div>
      </div>
    </main>
    <PreferenceDialog v-if="showSettings" @close="showSettings = false" />
  </div>
</template>
