<script setup>
import { onMounted, ref } from 'vue'
import { getPreferences, savePreferences } from '../api'

const emit = defineEmits(['close'])

const fields = [
  { key: 'home_city', label: '常驻城市', placeholder: '没说出发地时用它，例如 武汉' },
  { key: 'taste', label: '口味', placeholder: '例如 爱吃辣、偏清淡' },
  { key: 'allergy', label: '忌口过敏', placeholder: '例如 不吃香菜、海鲜过敏' },
  { key: 'interest', label: '兴趣', placeholder: '例如 自然风光、博物馆' },
  { key: 'pace', label: '节奏', placeholder: '例如 一天不要太赶' },
  { key: 'budget_habit', label: '预算档次', placeholder: '平价 / 中档 / 高档' },
  { key: 'lodging', label: '住宿', placeholder: '例如 安静、离地铁近' },
  { key: 'transport_go', label: '去程方式', placeholder: '例如 高铁' },
  { key: 'transport_back', label: '回程方式', placeholder: '例如 高铁' },
  { key: 'companions', label: '同行的人', placeholder: '例如 父母、同学' },
  { key: 'planning_habit', label: '规划习惯', placeholder: '例如 先订酒店再排景点' },
]

const form = ref(null)
const status = ref('')
const saving = ref(false)

onMounted(async () => {
  try {
    form.value = await getPreferences()
  } catch (e) {
    status.value = '读取失败：' + e.message
  }
})

async function save() {
  saving.value = true
  status.value = ''
  try {
    form.value = await savePreferences(form.value)
    status.value = '已保存'
  } catch (e) {
    status.value = '保存失败：' + e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog">
      <div class="dialog-head">
        <div>
          <h2>我的偏好</h2>
          <p>聊天中提到的口味、习惯会自动记在这里，规划时自动带上。你也可以直接改。</p>
        </div>
        <button class="ghost close" @click="emit('close')">✕</button>
      </div>

      <div v-if="!form" class="dialog-body muted">{{ status || '读取中…' }}</div>
      <form v-else class="dialog-body pref-grid" @submit.prevent="save">
        <label v-for="f in fields" :key="f.key" class="pref-field">
          <span>{{ f.label }}</span>
          <input v-model="form[f.key]" :placeholder="f.placeholder" />
        </label>
      </form>

      <div class="dialog-foot">
        <span class="pref-status">{{ form ? status : '' }}</span>
        <button class="btn-ghost" @click="emit('close')">关闭</button>
        <button class="btn-dark" :disabled="!form || saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </div>
  </div>
</template>
