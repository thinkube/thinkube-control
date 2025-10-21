<template>
  <div class="hero min-h-screen bg-base-200">
    <div class="hero-content">
      <div class="card">
        <div class="card-body items-center text-center">
          <span class="loading loading-spinner loading-lg text-primary" />
          <p class="mt-4">
            {{ message }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { handleAuthCallback } from '@/services/auth'

const router = useRouter()
const { t } = useI18n()
const message = ref(t('auth.loggingIn'))

onMounted(async () => {
  const urlParams = new URLSearchParams(window.location.search)
  const code = urlParams.get('code')
  const error = urlParams.get('error')
  
  if (error) {
    message.value = `${t('auth.loginFailed')}: ${error}`
    setTimeout(() => {
      router.push('/')
    }, 3000)
    return
  }
  
  if (!code) {
    message.value = t('auth.noCode')
    setTimeout(() => {
      router.push('/')
    }, 3000)
    return
  }
  
  try {
    await handleAuthCallback(code)
    message.value = t('auth.loginSuccess')
    // Small delay to ensure token is properly stored
    setTimeout(() => {
      // Check for intended route stored before redirect to login
      const intendedRoute = sessionStorage.getItem('intendedRoute')
      if (intendedRoute) {
        sessionStorage.removeItem('intendedRoute')
        router.push(intendedRoute)
      } else {
        // Default to dashboard if no intended route
        router.push('/dashboard')
      }
    }, 100)
  } catch (error) {
    console.error('Auth callback failed:', error)
    message.value = `${t('auth.loginFailed')}. ${t('auth.redirecting')}`
    setTimeout(() => {
      router.push('/')
    }, 3000)
  }
})
</script>