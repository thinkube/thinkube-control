import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '@/services/api'
import { useAuthStore } from './auth'

export const useHarborImagesStore = defineStore('harborImages', () => {
  // State
  const images = ref([])
  const jobs = ref([])
  const loading = ref(false)
  const error = ref(null)
  const stats = ref({
    total: 0,
    by_category: {
      core: 0,
      custom: 0,
      user: 0
    },
    protected: 0,
    vulnerable: 0
  })

  // Current filters
  const filters = ref({
    category: null,
    protected: null,
    search: ''
  })

  // Pagination
  const pagination = ref({
    skip: 0,
    limit: 50,
    total: 0
  })

  // Computed
  const coreImages = computed(() => images.value.filter(img => img.category === 'core'))
  const customImages = computed(() => images.value.filter(img => img.category === 'custom'))
  const userImages = computed(() => images.value.filter(img => img.category === 'user'))
  const protectedImages = computed(() => images.value.filter(img => img.protected))


  // Actions
  const fetchImages = async (options = {}) => {
    loading.value = true
    error.value = null

    try {
      const params = {
        skip: options.skip || pagination.value.skip,
        limit: options.limit || pagination.value.limit,
        ...filters.value
      }

      // Remove null/empty values
      Object.keys(params).forEach(key => {
        if (params[key] === null || params[key] === '') {
          delete params[key]
        }
      })

      const response = await api.get('/harbor/images', { params })

      images.value = response.data.images
      pagination.value.total = response.data.total
      pagination.value.skip = response.data.skip
      pagination.value.limit = response.data.limit

      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  const fetchImageStats = async () => {
    try {
      const response = await api.get('/harbor/stats/images')
      stats.value = response.data
      return response.data
    } catch (err) {
      console.error('Failed to fetch image stats:', err)
      console.error('Error response:', err.response?.data)
    }
  }

  const getImage = async (imageId) => {
    try {
      const response = await api.get(`/harbor/images/${imageId}`)
      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    }
  }

  const addImage = async (imageData) => {
    loading.value = true
    error.value = null

    try {
      const response = await api.post('/harbor/images', imageData)

      // Refresh the list
      await fetchImages()

      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  const updateImage = async (imageId, imageData) => {
    loading.value = true
    error.value = null

    try {
      const response = await api.put(`/harbor/images/${imageId}`, imageData)

      // Update in local state
      const index = images.value.findIndex(img => img.id === imageId)
      if (index !== -1) {
        images.value[index] = response.data
      }

      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  const deleteImage = async (imageId) => {
    loading.value = true
    error.value = null

    try {
      const response = await api.delete(`/harbor/images/${imageId}`)

      // Remove from local state
      images.value = images.value.filter(img => img.id !== imageId)

      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  const remirrorImage = async (imageId) => {
    loading.value = true
    error.value = null

    try {
      const response = await api.post(`/harbor/images/${imageId}/remirror`)
      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  const triggerMirror = async (mirrorRequest) => {
    loading.value = true
    error.value = null

    try {
      const response = await api.post('/harbor/images/mirror', mirrorRequest)

      // Add jobs to local state
      if (response.data.jobs) {
        jobs.value.push(...response.data.jobs)
      }

      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  const syncWithHarbor = async () => {
    loading.value = true
    error.value = null

    try {
      const response = await api.post('/harbor/images/sync', {})

      // Refresh the list
      await fetchImages()

      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  const fetchJobs = async (options = {}) => {
    try {
      const params = {
        skip: options.skip || 0,
        limit: options.limit || 50,
        status: options.status,
        job_type: options.job_type
      }

      // Remove null values
      Object.keys(params).forEach(key => {
        if (params[key] === null || params[key] === undefined) {
          delete params[key]
        }
      })

      const response = await api.get('/harbor/jobs', { params })

      jobs.value = response.data.jobs
      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    }
  }

  const getJobStatus = async (jobId) => {
    try {
      const response = await api.get(`/harbor/jobs/${jobId}`)

      // Update in local state
      const index = jobs.value.findIndex(job => job.id === jobId)
      if (index !== -1) {
        jobs.value[index] = response.data
      }

      return response.data
    } catch (err) {
      error.value = err.response?.data?.detail || err.message
      throw err
    }
  }

  const checkHarborHealth = async () => {
    try {
      const response = await api.get('/harbor/health')
      return response.data
    } catch (err) {
      console.error('Failed to check Harbor health:', err)
      return { status: 'unknown', error: err.message }
    }
  }

  // Filter helpers
  const setFilter = (key, value) => {
    filters.value[key] = value
    pagination.value.skip = 0 // Reset pagination when filtering
    return fetchImages()
  }

  const clearFilters = () => {
    filters.value = {
      category: null,
      protected: null,
      search: ''
    }
    pagination.value.skip = 0
    return fetchImages()
  }

  // Pagination helpers
  const nextPage = () => {
    if (pagination.value.skip + pagination.value.limit < pagination.value.total) {
      pagination.value.skip += pagination.value.limit
      return fetchImages()
    }
  }

  const previousPage = () => {
    if (pagination.value.skip > 0) {
      pagination.value.skip = Math.max(0, pagination.value.skip - pagination.value.limit)
      return fetchImages()
    }
  }

  const goToPage = (page) => {
    pagination.value.skip = (page - 1) * pagination.value.limit
    return fetchImages()
  }

  return {
    // State
    images,
    jobs,
    loading,
    error,
    stats,
    filters,
    pagination,

    // Computed
    coreImages,
    customImages,
    userImages,
    protectedImages,

    // Actions
    fetchImages,
    fetchImageStats,
    getImage,
    addImage,
    updateImage,
    deleteImage,
    remirrorImage,
    triggerMirror,
    syncWithHarbor,
    fetchJobs,
    getJobStatus,
    checkHarborHealth,

    // Filter helpers
    setFilter,
    clearFilters,

    // Pagination helpers
    nextPage,
    previousPage,
    goToPage
  }
})