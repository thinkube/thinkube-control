// src/views/__tests__/Dashboard.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Dashboard from '../Dashboard.vue'
import { useServicesStore } from '@/stores/services'
import { useAuthStore } from '@/stores/auth'

// Mock the stores
vi.mock('@/stores/services')
vi.mock('@/stores/auth')

// Mock child components
vi.mock('@/components/ServiceCard.vue', () => ({
  default: {
    name: 'ServiceCard',
    template: '<div class="service-card">{{ service.display_name }}</div>',
    props: ['service']
  }
}))

vi.mock('@/components/FavoriteServiceCard.vue', () => ({
  default: {
    name: 'FavoriteServiceCard',
    template: '<div class="favorite-service-card">{{ service.display_name }}</div>',
    props: ['service']
  }
}))

vi.mock('@/components/ServiceDetailsModal.vue', () => ({
  default: {
    name: 'ServiceDetailsModal',
    template: '<div class="modal">Service Details</div>',
    props: ['service']
  }
}))

// Mock Element Plus
vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    error: vi.fn()
  },
  ElMessageBox: {
    confirm: vi.fn()
  }
}))

describe('Dashboard.vue', () => {
  let wrapper
  let mockServicesStore
  let mockAuthStore

  const mockServices = [
    {
      id: '1',
      name: 'keycloak',
      display_name: 'Keycloak',
      description: 'Identity management',
      type: 'core',
      category: 'infrastructure',
      url: 'https://auth.example.com',
      is_enabled: true,
      can_be_disabled: false
    },
    {
      id: '2',
      name: 'prometheus',
      display_name: 'Prometheus',
      description: 'Monitoring',
      type: 'optional',
      category: 'monitoring',
      url: 'https://prometheus.example.com',
      is_enabled: true,
      can_be_disabled: true
    },
    {
      id: '3',
      name: 'my-app',
      display_name: 'My App',
      description: 'User application',
      type: 'user_app',
      category: 'application',
      url: 'https://my-app.example.com',
      is_enabled: true,
      can_be_disabled: true
    }
  ]

  beforeEach(() => {
    // Create a fresh pinia instance
    setActivePinia(createPinia())
    
    // Mock services store
    mockServicesStore = {
      services: mockServices,
      loading: false,
      error: null,
      categoryFilter: null,
      categories: ['infrastructure', 'monitoring', 'application'],
      coreServices: mockServices.filter(s => s.type === 'core'),
      optionalServices: mockServices.filter(s => s.type === 'optional'),
      userApps: mockServices.filter(s => s.type === 'user_app'),
      filteredServices: mockServices,
      favoriteServices: [],
      favoriteServicesComputed: [],
      fetchServices: vi.fn(),
      setCategoryFilter: vi.fn(),
      toggleService: vi.fn(),
      restartService: vi.fn(),
      syncServices: vi.fn(),
      fetchServiceDetails: vi.fn(),
      fetchHealthHistory: vi.fn(),
      fetchFavorites: vi.fn(),
      addToFavorites: vi.fn(),
      removeFromFavorites: vi.fn(),
      toggleFavorite: vi.fn()
    }
    
    // Mock auth store
    mockAuthStore = {
      hasRole: vi.fn((role) => role === 'admin')
    }
    
    useServicesStore.mockReturnValue(mockServicesStore)
    useAuthStore.mockReturnValue(mockAuthStore)
  })

  it('fetches services on mount', async () => {
    wrapper = mount(Dashboard)
    await flushPromises()
    
    expect(mockServicesStore.fetchServices).toHaveBeenCalled()
  })

  it('displays loading state', async () => {
    mockServicesStore.loading = true
    wrapper = mount(Dashboard)
    
    expect(wrapper.find('.loading-spinner').exists()).toBe(true)
  })

  it('displays error state', async () => {
    mockServicesStore.loading = false
    mockServicesStore.error = 'Failed to load services'
    wrapper = mount(Dashboard)
    
    expect(wrapper.find('.alert-error').exists()).toBe(true)
    expect(wrapper.text()).toContain('Failed to load services')
  })

  it('displays all services when All Services tab is selected', async () => {
    wrapper = mount(Dashboard)
    await flushPromises()
    
    // Click on "All Services" tab to see the services
    const allTab = wrapper.findAll('.tab')[1] // Second tab is "All Services"
    await allTab.trigger('click')
    await flushPromises()
    
    // Should show category filter
    expect(wrapper.text()).toContain('dashboard.categories.all')
    
    // Should show all services that have URLs
    const cards = wrapper.findAll('.service-card')
    expect(cards).toHaveLength(mockServices.filter(s => s.url).length)
  })

  it('filters services by category', async () => {
    // Set category filter to 'monitoring'
    mockServicesStore.categoryFilter = 'monitoring'
    mockServicesStore.filteredServices = mockServices.filter(s => s.category === 'monitoring')
    
    wrapper = mount(Dashboard)
    await flushPromises()
    
    // Click on "All Services" tab to see the services
    const allTab = wrapper.findAll('.tab')[1]
    await allTab.trigger('click')
    await flushPromises()
    
    const cards = wrapper.findAll('.service-card')
    expect(cards).toHaveLength(mockServicesStore.filteredServices.filter(s => s.url).length)
  })

  it('shows sync button for all users', async () => {
    wrapper = mount(Dashboard)
    await flushPromises()
    
    const syncButton = wrapper.find('button[class*="btn-ghost"]')
    expect(syncButton.exists()).toBe(true)
    expect(syncButton.text()).toContain('dashboard.syncServices')
  })

  it('sync button is available regardless of admin role', async () => {
    mockAuthStore.hasRole = vi.fn(() => false)
    
    wrapper = mount(Dashboard)
    await flushPromises()
    
    const syncButton = wrapper.find('button[class*="btn-ghost"]')
    expect(syncButton.exists()).toBe(true)
  })

  it('calls sync services when sync button clicked', async () => {
    wrapper = mount(Dashboard)
    await flushPromises()
    
    const syncButton = wrapper.find('button[class*="btn-ghost"]')
    await syncButton.trigger('click')
    
    expect(mockServicesStore.syncServices).toHaveBeenCalled()
  })
})