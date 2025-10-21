<template>
  <div class="min-h-screen bg-base-100">
    <!-- Loading overlay -->
    <div
      v-if="authStore.loading"
      class="fixed inset-0 z-50 flex items-center justify-center bg-base-100/80"
    >
      <div class="text-center">
        <span class="loading loading-spinner loading-lg" />
        <p class="mt-4 text-lg">
          {{ t('errors.general.loading') }}
        </p>
      </div>
    </div>

    <!-- Main content -->
    <template v-else-if="authStore.user">
      <NavBar :user="authStore.user" />
      
      <main class="min-h-screen">
        <router-view />
      </main>
    </template>

    <!-- Error state -->
    <div
      v-else
      class="flex items-center justify-center min-h-screen"
    >
      <div class="text-center">
        <h1 class="text-2xl font-bold mb-4">
          {{ t('errors.loadingApp.title') }}
        </h1>
        <p class="mb-4">
          {{ t('errors.loadingApp.message') }}
        </p>
        <button
          class="btn btn-primary"
          @click="reload"
        >
          {{ t('errors.loadingApp.reload') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import NavBar from '@/components/NavBar.vue'

const { t } = useI18n()
const authStore = useAuthStore()

onMounted(async () => {
  if (!authStore.user) {
    await authStore.fetchUser()
  }
})

const reload = () => {
  window.location.reload()
}
</script>