<!-- src/App.vue -->
<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'

onMounted(() => {
  // Check if user has a theme preference stored
  const storedTheme = localStorage.getItem('theme')
  if (storedTheme) {
    document.documentElement.setAttribute('data-theme', storedTheme)
  } else {
    // Check for system preference
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    const theme = prefersDark ? 'thinkube-dark' : 'thinkube'
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }
})
</script>

<style>
@import './assets/styles.css';
</style>