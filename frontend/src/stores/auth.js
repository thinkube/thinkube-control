import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getUserInfo, logout as authLogout } from '@/services/auth'
import { getToken } from '@/services/tokenManager'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)
  const accessToken = ref(null)
  const refreshToken = ref(null)
  const loading = ref(false)
  const error = ref(null)

  const isAuthenticated = computed(() => !!accessToken.value)
  const userRoles = computed(() => user.value?.roles || [])
  const hasRole = computed(() => (role) => userRoles.value.includes(role))

  function setTokens(access, refresh) {
    accessToken.value = access
    refreshToken.value = refresh
    // Store in localStorage for persistence
    if (access) {
      localStorage.setItem('access_token', access)
    }
    if (refresh) {
      localStorage.setItem('refresh_token', refresh)
    }
  }

  function setUser(userData) {
    user.value = userData
  }

  function clearAuth() {
    user.value = null
    accessToken.value = null
    refreshToken.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }

  function loadFromStorage() {
    const storedAccessToken = localStorage.getItem('access_token')
    const storedRefreshToken = localStorage.getItem('refresh_token')
    if (storedAccessToken) {
      accessToken.value = storedAccessToken
    }
    if (storedRefreshToken) {
      refreshToken.value = storedRefreshToken
    }
  }

  async function fetchUser() {
    loading.value = true
    error.value = null
    
    try {
      // First ensure we have a token
      const token = getToken()
      if (!token) {
        throw new Error('No authentication token')
      }
      
      // Update our local token
      accessToken.value = token
      
      // Fetch user info
      const userInfo = await getUserInfo()
      user.value = userInfo
    } catch (err) {
      console.error('Failed to fetch user:', err)
      error.value = err.message
      user.value = null
      throw err
    } finally {
      loading.value = false
    }
  }

  async function logout() {
    user.value = null
    accessToken.value = null
    refreshToken.value = null
    await authLogout()
  }

  return {
    user,
    accessToken,
    refreshToken,
    isAuthenticated,
    userRoles,
    hasRole,
    loading,
    error,
    setTokens,
    setUser,
    clearAuth,
    loadFromStorage,
    fetchUser,
    logout
  }
})