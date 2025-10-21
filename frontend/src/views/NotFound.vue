<!-- src/views/NotFound.vue -->
<template>
  <div class="hero min-h-screen bg-base-200">
    <div class="hero-content text-center">
      <div class="card">
        <div class="card-body items-center">
          <div class="avatar placeholder mb-6">
            <div class="bg-primary text-primary-content rounded-full w-32">
              <span class="text-6xl">404</span>
            </div>
          </div>
          <h1 class="card-title text-2xl mb-4">
            {{ t('errors.notFound.heading') }}
          </h1>
          <p class="mb-6">
            {{ t('errors.notFound.message') }}
          </p>
          
          <router-link
            to="/"
            class="btn btn-primary"
          >
            {{ t('errors.notFound.backButton') }}
          </router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>