import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getDashboards, getDashboardCategories } from '@/services/api'

export const useDashboardStore = defineStore('dashboards', () => {
  const dashboards = ref([])
  const categories = ref([])
  const loading = ref(false)
  const error = ref(null)
  const selectedCategory = ref(null)

  const filteredDashboards = computed(() => {
    if (!selectedCategory.value) {
      return dashboards.value
    }
    return dashboards.value.filter(d => d.category === selectedCategory.value)
  })

  async function fetchDashboards() {
    loading.value = true
    error.value = null
    
    try {
      const [dashboardData, categoryData] = await Promise.all([
        getDashboards(),
        getDashboardCategories()
      ])
      
      dashboards.value = dashboardData
      categories.value = categoryData
    } catch (err) {
      console.error('Failed to fetch dashboards:', err)
      error.value = err.message
    } finally {
      loading.value = false
    }
  }

  function setSelectedCategory(category) {
    selectedCategory.value = category
  }

  function clearSelectedCategory() {
    selectedCategory.value = null
  }

  return {
    dashboards,
    categories,
    loading,
    error,
    selectedCategory,
    filteredDashboards,
    fetchDashboards,
    setSelectedCategory,
    clearSelectedCategory
  }
})