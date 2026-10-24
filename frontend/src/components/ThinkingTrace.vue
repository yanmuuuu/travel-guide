<script setup>
defineProps({
  steps: { type: Array, required: true },
  live: { type: Boolean, default: false },
})

const TOOL_NAMES = {
  task: '派出子代理',
  ask: '询问你',
  write_todos: '更新计划',
  weather: '查天气',
  sight: '查景点',
  place: '查地点详情',
  notes: '搜小红书',
  web: '搜网页',
  road: '查市内交通',
  route_map: '画路线地图',
  train: '查火车票',
  transfer: '查中转',
  flight: '查机票',
  hotel: '查酒店',
  food: '找餐馆',
  read_file: '读文件',
  write_file: '写文件',
  edit_file: '改文件',
  ls: '列文件',
}

const AGENT_NAMES = {
  route: '路线',
  traffic: '交通',
  stay: '住宿',
  food: '餐饮',
}

const toolLabel = (name) => TOOL_NAMES[name] || name
const agentLabel = (name) => AGENT_NAMES[name] || name

function summary(step) {
  const a = step.args || {}
  if (step.name === 'task') return `${agentLabel(a.subagent_type || '')}：${a.description || ''}`
  if (step.name === 'ask') return a.question || ''
  if (step.name === 'write_todos') return (a.todos || []).map((t) => t.content).join(' · ')
  return Object.values(a)
    .map((v) => (typeof v === 'string' ? v : JSON.stringify(v)))
    .join('  ')
}

const pretty = (args) => JSON.stringify(args, null, 2)
</script>

<template>
  <ol class="trace">
    <li v-for="(s, i) in steps" :key="i" :class="['trace-item', s.kind]">
      <span v-if="s.agent && s.agent !== '主代理'" class="agent-tag">{{ agentLabel(s.agent) }}</span>

      <div v-if="s.kind === 'thought'" class="thought">{{ s.text }}</div>

      <details v-else-if="s.kind === 'call'" class="call">
        <summary>
          <span :class="['state', s.result === undefined ? (live ? 'running' : 'idle') : s.error ? 'fail' : 'ok']"></span>
          <span class="tool-name">{{ toolLabel(s.name) }}</span>
          <span class="tool-args">{{ summary(s) }}</span>
        </summary>
        <div class="call-body">
          <div class="call-label">参数</div>
          <pre>{{ pretty(s.args) }}</pre>
          <template v-if="s.result !== undefined">
            <div class="call-label">返回</div>
            <pre :class="{ err: s.error }">{{ s.result || '（空）' }}</pre>
          </template>
        </div>
      </details>
    </li>
  </ol>
</template>
