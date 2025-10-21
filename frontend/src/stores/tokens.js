import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getTokens as getTokensAPI, createToken as createTokenAPI, revokeToken as revokeTokenAPI } from '@/services/api'
import { useAuthStore } from './auth'

export const useTokenStore = defineStore('tokens', () => {
  const tokens = ref([])
  const loading = ref(false)
  const error = ref(null)
  
  const authStore = useAuthStore()
  
  async function fetchTokens() {
    if (!authStore.accessToken) {
      console.warn('No access token available, skipping token fetch')
      return
    }
    
    loading.value = true
    error.value = null
    
    try {
      const data = await getTokensAPI(authStore.accessToken)
      tokens.value = data
    } catch (err) {
      console.error('Failed to fetch tokens:', err)
      error.value = err.message
    } finally {
      loading.value = false
    }
  }
  
  async function createToken(tokenData) {
    if (!authStore.accessToken) {
      throw new Error('No authentication token')
    }
    
    const data = await createTokenAPI(tokenData, authStore.accessToken)
    
    await fetchTokens()
    return data
  }
  
  async function revokeToken(tokenId) {
    if (!authStore.accessToken) {
      throw new Error('No authentication token')
    }

    await revokeTokenAPI(tokenId, authStore.accessToken)

    await fetchTokens()
  }

  async function revealToken(tokenId) {
    if (!authStore.accessToken) {
      throw new Error('No authentication token')
    }

    const { revealToken: revealTokenAPI } = await import('@/services/api')
    const data = await revealTokenAPI(tokenId, authStore.accessToken)
    return data
  }

  return {
    tokens,
    loading,
    error,
    fetchTokens,
    createToken,
    revokeToken,
    revealToken
  }
})