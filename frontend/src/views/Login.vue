<!-- src/views/Login.vue -->
<template>
  <div class="hero min-h-screen bg-base-200">
    <div class="hero-content">
      <div class="card w-96 bg-base-100 shadow-xl">
        <div class="card-body">
          <h2 class="card-title justify-center mb-4">
            {{ t('auth.loginTitle') }}
          </h2>
          <p class="text-center mb-6">
            {{ t('auth.loginMessage') }}
          </p>
          
          <div
            v-if="loading"
            class="text-center mb-4"
          >
            <span class="loading loading-spinner loading-md" />
          </div>
          
          <div
            v-if="error"
            class="alert alert-error alert-soft mb-4"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              class="stroke-current shrink-0 h-6 w-6"
              fill="none"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            <span>{{ errorMessage }}</span>
          </div>
          
          <div class="card-actions">
            <button 
              class="btn btn-primary btn-block" 
              :disabled="loading"
              @click="login"
            >
              {{ t('auth.loginButton') }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { redirectToLogin } from '@/services/auth'

const { t } = useI18n()

const loading = ref(false)
const error = ref(false)
const errorMessage = ref('')

const login = async () => {
  loading.value = true
  error.value = false
  
  try {
    await redirectToLogin()
    // Successful login redirects to dashboard
  } catch (err) {
    error.value = true
    errorMessage.value = err.message || t('auth.loginFailed')
    loading.value = false
  }
}
</script>