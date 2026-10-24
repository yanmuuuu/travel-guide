<script setup>
import { ref } from 'vue'
import { login, register } from '../api'

const emit = defineEmits(['done'])
const mode = ref('login')
const username = ref('')
const password = ref('')
const confirm = ref('')
const error = ref('')
const loading = ref(false)

function toggle() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  error.value = ''
}

async function submit() {
  error.value = ''
  if (mode.value === 'register' && password.value !== confirm.value) {
    error.value = '两次输入的密码不一样'
    return
  }
  loading.value = true
  try {
    const fn = mode.value === 'login' ? login : register
    emit('done', await fn(username.value, password.value))
  } catch (e) {
    error.value = e.message === 'Failed to fetch' ? '连不上后端' : e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth">
    <form class="auth-card" @submit.prevent="submit">
      <div class="auth-logo">◎</div>
      <h1>{{ mode === 'login' ? '欢迎回来' : '创建账号' }}</h1>
      <p class="auth-sub">{{ mode === 'login' ? '登录后继续规划你的旅行' : '记住你的口味和习惯，下次出行自动带上' }}</p>

      <input v-model="username" class="auth-input" placeholder="用户名" autocomplete="username" required />
      <input
        v-model="password"
        class="auth-input"
        type="password"
        placeholder="密码"
        :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
        required
      />
      <input
        v-if="mode === 'register'"
        v-model="confirm"
        class="auth-input"
        type="password"
        placeholder="确认密码"
        autocomplete="new-password"
        required
      />

      <p v-if="error" class="auth-error">{{ error }}</p>

      <button class="auth-btn" type="submit" :disabled="loading || !username || !password">
        {{ loading ? '请稍候…' : mode === 'login' ? '登录' : '注册' }}
      </button>

      <p class="auth-switch">
        {{ mode === 'login' ? '还没有账号？' : '已经有账号了？' }}
        <button type="button" @click="toggle">{{ mode === 'login' ? '注册' : '登录' }}</button>
      </p>
    </form>
  </div>
</template>
