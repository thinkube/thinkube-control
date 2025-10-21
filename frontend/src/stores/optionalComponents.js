// src/stores/optionalComponents.js
import { defineStore } from 'pinia'
import { api } from '@/services/api'

export const useOptionalComponentsStore = defineStore('optionalComponents', {
  state: () => ({
    components: [],
    loading: false,
    error: null
  }),

  getters: {
    installedComponents: (state) => {
      return state.components.filter(c => c.installed)
    },
    
    availableComponents: (state) => {
      return state.components.filter(c => !c.installed)
    },
    
    componentsByCategory: (state) => {
      const grouped = {}
      state.components.forEach(component => {
        if (!grouped[component.category]) {
          grouped[component.category] = []
        }
        grouped[component.category].push(component)
      })
      return grouped
    }
  },

  actions: {
    async listComponents() {
      this.loading = true
      this.error = null
      
      try {
        const response = await api.get('/optional-components/list')
        this.components = response.data.components
        return response.data
      } catch (error) {
        console.error('Failed to list optional components:', error)
        this.error = error.message
        throw error
      } finally {
        this.loading = false
      }
    },

    async getComponentInfo(componentName) {
      try {
        const response = await api.get(`/optional-components/${componentName}/info`)
        return response.data
      } catch (error) {
        console.error(`Failed to get info for component ${componentName}:`, error)
        throw error
      }
    },

    async installComponent(componentName, parameters = {}) {
      try {
        const response = await api.post(
          `/optional-components/${componentName}/install`,
          {
            parameters,
            force: false
          }
        )
        
        // Refresh component list after installation starts
        setTimeout(() => this.listComponents(), 2000)
        
        return response.data
      } catch (error) {
        console.error(`Failed to install component ${componentName}:`, error)
        throw error
      }
    },

    async uninstallComponent(componentName) {
      try {
        const response = await api.delete(
          `/optional-components/${componentName}`
        )
        
        // Refresh component list after uninstallation starts
        setTimeout(() => this.listComponents(), 2000)
        
        return response.data
      } catch (error) {
        console.error(`Failed to uninstall component ${componentName}:`, error)
        throw error
      }
    },

    async getComponentStatus(componentName) {
      try {
        const response = await api.get(
          `/optional-components/${componentName}/status`
        )
        return response.data
      } catch (error) {
        console.error(`Failed to get status for component ${componentName}:`, error)
        throw error
      }
    }
  }
})