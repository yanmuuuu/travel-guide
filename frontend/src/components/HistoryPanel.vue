<script setup>
import { onMounted, ref } from 'vue'
import { getGuide, listGuides } from '../api'

const emit = defineEmits(['open'])
const items = ref([])
const error = ref('')

async function refresh() {
  try {
    items.value = await listGuides()
    error.value = ''
  } catch (e) {
    error.value = '读取失败'
  }
}

async function open(id) {
  try {
    emit('open', await getGuide(id))
  } catch (e) {
    error.value = '读取失败'
  }
}

onMounted(refresh)
defineExpose({ refresh })
</script>

<template>
  <section class="history">
    <div class="history-head">
      <span>行程</span>
      <button class="ghost tiny" @click="refresh" title="刷新">↻</button>
    </div>
    <p v-if="!items.length" class="side-note">还没有保存的行程</p>
    <ul>
      <li v-for="g in items" :key="g.id" @click="open(g.id)" :title="g.title">
        <span class="h-title">{{ g.title }}</span>
        <span class="h-date">{{ new Date(g.created_at).toLocaleDateString() }}</span>
      </li>
    </ul>
  </section>
</template>
