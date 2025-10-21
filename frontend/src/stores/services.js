import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '@/services/api'

export const useServicesStore = defineStore('services', () => {
  // State
  const services = ref([])
  const loading = ref(false)
  const error = ref(null)
  const selectedServiceId = ref(null)
  const serviceDetails = ref({})
  const healthHistory = ref({})
  const favoriteServices = ref([])
  
  // Filters
  const categoryFilter = ref(null)
  const enabledFilter = ref(null)
  
  // Computed
  const filteredServices = computed(() => {
    let result = services.value
    
    if (categoryFilter.value) {
      result = result.filter(s => s.category === categoryFilter.value)
    }
    
    if (enabledFilter.value !== null) {
      result = result.filter(s => s.is_enabled === enabledFilter.value)
    }
    
    return result
  })
  
  const categories = computed(() => {
    const cats = new Set()
    services.value.forEach(s => {
      if (s.category) cats.add(s.category)
    })
    return Array.from(cats).sort()
  })
  
  const selectedService = computed(() => {
    return services.value.find(s => s.id === selectedServiceId.value)
  })
  
  const coreServices = computed(() => 
    services.value.filter(s => s.type === 'core')
  )
  
  const optionalServices = computed(() => 
    services.value.filter(s => s.type === 'optional')
  )
  
  const userApps = computed(() => 
    services.value.filter(s => s.type === 'user_app')
  )
  
  const favoriteServicesComputed = computed(() => 
    services.value.filter(s => s.is_favorite)
  )
  
  // Actions
  async function fetchServices() {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.get('/services/')
      services.value = response.data.services
    } catch (err) {
      console.error('Failed to fetch services:', err)
      error.value = err.response?.data?.detail || err.message
    } finally {
      loading.value = false
    }
  }
  
  async function fetchServiceDetails(serviceId) {
    try {
      const response = await api.get(`/services/${serviceId}`)
      serviceDetails.value[serviceId] = response.data
      return response.data
    } catch (err) {
      console.error('Failed to fetch service details:', err)
      throw err
    }
  }
  
  async function toggleService(serviceId, enabled, reason = null) {
    try {
      const response = await api.post(`/services/${serviceId}/toggle`, {
        is_enabled: enabled,
        reason
      })
      
      // Update service in list
      const index = services.value.findIndex(s => s.id === serviceId)
      if (index !== -1) {
        services.value[index] = response.data
      }
      
      // Update details if cached
      if (serviceDetails.value[serviceId]) {
        serviceDetails.value[serviceId] = response.data
      }
      
      return response.data
    } catch (err) {
      console.error('Failed to toggle service:', err)
      throw err
    }
  }
  
  async function restartService(serviceId) {
    try {
      const response = await api.post(`/services/${serviceId}/restart`)
      
      // Trigger health check after restart
      setTimeout(() => {
        triggerHealthCheck(serviceId)
      }, 5000)
      
      return response.data
    } catch (err) {
      console.error('Failed to restart service:', err)
      throw err
    }
  }
  
  async function fetchHealthHistory(serviceId, hours = 24) {
    try {
      const response = await api.get(`/services/${serviceId}/health`, {
        params: { hours }
      })
      healthHistory.value[serviceId] = response.data
      return response.data
    } catch (err) {
      console.error('Failed to fetch health history:', err)
      throw err
    }
  }
  
  async function triggerHealthCheck(serviceId) {
    try {
      const response = await api.post(`/services/${serviceId}/health-check`)
      
      // Update service health status if successful
      const service = services.value.find(s => s.id === serviceId)
      if (service && response.data.status) {
        service.latest_health = {
          status: response.data.status,
          checked_at: response.data.checked_at || new Date().toISOString()
        }
      }
      
      return response.data
    } catch (err) {
      console.error('Failed to trigger health check:', err)
      throw err
    }
  }
  
  async function checkServiceName(name, type) {
    try {
      const response = await api.post('/services/check-name', { name, type })
      return response.data
    } catch (err) {
      console.error('Failed to check service name:', err)
      throw err
    }
  }
  
  async function syncServices() {
    try {
      const response = await api.post('/services/sync')
      // Refresh services after sync
      await fetchServices()
      return response.data
    } catch (err) {
      console.error('Failed to sync services:', err)
      throw err
    }
  }
  
  function setCategoryFilter(category) {
    categoryFilter.value = category
  }
  
  function setEnabledFilter(enabled) {
    enabledFilter.value = enabled
  }
  
  function clearFilters() {
    categoryFilter.value = null
    enabledFilter.value = null
  }
  
  function selectService(serviceId) {
    selectedServiceId.value = serviceId
  }
  
  async function fetchFavorites() {
    try {
      const response = await api.get('/services/favorites')
      favoriteServices.value = response.data.services
      return response.data
    } catch (err) {
      console.error('Failed to fetch favorites:', err)
      throw err
    }
  }
  
  async function addToFavorites(serviceId) {
    try {
      const response = await api.post(`/services/${serviceId}/favorite`)
      
      // Update the service in the main list
      const index = services.value.findIndex(s => s.id === serviceId)
      if (index !== -1) {
        services.value[index].is_favorite = true
      }
      
      return response.data
    } catch (err) {
      console.error('Failed to add to favorites:', err)
      throw err
    }
  }
  
  async function removeFromFavorites(serviceId) {
    try {
      await api.delete(`/services/${serviceId}/favorite`)
      
      // Update the service in the main list
      const index = services.value.findIndex(s => s.id === serviceId)
      if (index !== -1) {
        services.value[index].is_favorite = false
      }
      
    } catch (err) {
      console.error('Failed to remove from favorites:', err)
      throw err
    }
  }
  
  async function toggleFavorite(service) {
    if (service.is_favorite) {
      await removeFromFavorites(service.id)
    } else {
      await addToFavorites(service.id)
    }
  }
  
  async function reorderFavorites(serviceIds) {
    try {
      await api.put('/services/favorites/reorder', serviceIds)
      // No need to refresh as the order is already updated in the UI
    } catch (err) {
      console.error('Failed to reorder favorites:', err)
      throw err
    }
  }

  async function describePod(serviceId, podName) {
    try {
      const response = await api.get(`/services/${serviceId}/pods/${podName}/describe`)
      return response.data
    } catch (err) {
      console.error('Failed to describe pod:', err)
      throw err
    }
  }

  async function getContainerLogs(serviceId, podName, containerName, lines = 100) {
    try {
      const response = await api.get(
        `/services/${serviceId}/pods/${podName}/containers/${containerName}/logs`,
        { params: { lines } }
      )
      return response.data
    } catch (err) {
      console.error('Failed to get container logs:', err)
      throw err
    }
  }
  
  return {
    // State
    services,
    loading,
    error,
    selectedServiceId,
    serviceDetails,
    healthHistory,
    favoriteServices,
    
    // Filters
    categoryFilter,
    enabledFilter,
    
    // Computed
    filteredServices,
    categories,
    selectedService,
    coreServices,
    optionalServices,
    userApps,
    favoriteServicesComputed,
    
    // Actions
    fetchServices,
    fetchServiceDetails,
    toggleService,
    restartService,
    fetchHealthHistory,
    triggerHealthCheck,
    checkServiceName,
    syncServices,
    setCategoryFilter,
    setEnabledFilter,
    clearFilters,
    selectService,
    fetchFavorites,
    addToFavorites,
    removeFromFavorites,
    toggleFavorite,
    reorderFavorites,
    describePod,
    getContainerLogs
  }
})