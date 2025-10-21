// src/views/__tests__/ApiTokens.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ApiTokens from '../ApiTokens.vue'
import { useAuthStore } from '@/stores/auth'
import { useTokenStore } from '@/stores/tokens'
// Mock axios
vi.mock('axios')

// Mock the token store
vi.mock('@/stores/tokens')

describe('ApiTokens.vue', () => {
  let wrapper
  let authStore
  let mockTokenStore

  const mockTokens = [
    {
      id: '1',
      name: 'Test Token',
      created_at: '2024-01-01T00:00:00Z',
      expires_at: '2024-12-31T23:59:59Z',
      last_used: '2024-06-01T12:00:00Z',
      scopes: ['read', 'write'],
      is_active: true
    }
  ]

  beforeEach(() => {
    // Create a fresh pinia instance
    setActivePinia(createPinia())
    
    // Get auth store and set initial state
    authStore = useAuthStore()
    authStore.setTokens('mock-access-token', 'mock-refresh-token')

    // Setup mock token store
    mockTokenStore = {
      tokens: mockTokens,
      loading: false,
      error: null,
      fetchTokens: vi.fn().mockResolvedValue(),
      createToken: vi.fn().mockResolvedValue({
        id: '2',
        name: 'New Token',
        token: 'new-token-value',
        created_at: '2024-06-17T00:00:00Z',
        expires_at: '2024-12-31T23:59:59Z',
        scopes: []
      }),
      revokeToken: vi.fn().mockResolvedValue()
    }
    
    useTokenStore.mockReturnValue(mockTokenStore)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('should render without tokens when unauthenticated', async () => {
    // Clear auth to test unauthenticated state
    authStore.clearAuth()
    
    wrapper = mount(ApiTokens)
    await flushPromises()
    
    // The component should still render
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('h1').exists()).toBe(true)
  })

  it('renders the component with auth store', async () => {
    wrapper = mount(ApiTokens)
    await flushPromises()
    
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('h1').exists()).toBe(true)
  })

  it('loads tokens on mount', async () => {
    wrapper = mount(ApiTokens)
    await flushPromises()

    // Verify fetchTokens was called
    expect(mockTokenStore.fetchTokens).toHaveBeenCalled()

    // Check that tokens are displayed
    const tokenCards = wrapper.findAll('[data-testid="token-card"]')
    expect(tokenCards).toHaveLength(1)
    expect(tokenCards[0].text()).toContain('Test Token')
  })

  it('creates a new token', async () => {
    wrapper = mount(ApiTokens)
    await flushPromises()

    // Fill in the form
    const nameInput = wrapper.find('input#token-name')
    await nameInput.setValue('New Test Token')

    // Submit the form
    const createButton = wrapper.find('button.btn-primary')
    await createButton.trigger('click')
    await flushPromises()

    // Verify createToken was called
    expect(mockTokenStore.createToken).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'New Test Token'
      })
    )

    // Verify the new token is displayed
    expect(wrapper.text()).toContain('new-token-value')
  })

  it('deletes a token', async () => {
    // Mock window.confirm to return true
    const originalConfirm = window.confirm
    window.confirm = vi.fn(() => true)
    
    wrapper = mount(ApiTokens)
    await flushPromises()

    // Find and click delete button
    const deleteButton = wrapper.find('button.btn-error')
    await deleteButton.trigger('click')
    await flushPromises()

    // Verify confirm was called
    expect(window.confirm).toHaveBeenCalled()

    // Verify revokeToken was called
    expect(mockTokenStore.revokeToken).toHaveBeenCalledWith('1')
    
    // Restore original confirm
    window.confirm = originalConfirm
  })

  it('handles API errors gracefully', async () => {
    // Mock console.error to avoid test output noise
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    
    // Make createToken reject
    mockTokenStore.createToken.mockRejectedValue(new Error('API Error'))

    wrapper = mount(ApiTokens)
    await flushPromises()

    // Fill in the form and try to create
    const nameInput = wrapper.find('input#token-name')
    await nameInput.setValue('Error Test Token')
    const createButton = wrapper.find('button.btn-primary')
    await createButton.trigger('click')
    await flushPromises()

    // Component should still render
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('h1').exists()).toBe(true)
    
    // Error should be logged
    expect(consoleError).toHaveBeenCalledWith('Failed to create token:', expect.any(Error))
    
    consoleError.mockRestore()
  })

  it('validates token creation input', async () => {
    wrapper = mount(ApiTokens)
    await flushPromises()

    // Check that create button is disabled when name is empty
    const createButton = wrapper.find('button.btn-primary')
    expect(createButton.attributes('disabled')).toBeDefined()

    // Enter a name and check button is enabled
    const nameInput = wrapper.find('input#token-name')
    await nameInput.setValue('Test Token')
    await wrapper.vm.$nextTick()
    
    expect(createButton.attributes('disabled')).toBeUndefined()
    
    // Clear the name and check button is disabled again
    await nameInput.setValue('')
    await wrapper.vm.$nextTick()
    
    expect(createButton.attributes('disabled')).toBeDefined()
    expect(mockTokenStore.createToken).not.toHaveBeenCalled()
  })

  it('copies token to clipboard', async () => {
    // Mock clipboard API
    const mockWriteText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: {
        writeText: mockWriteText
      }
    })

    // Mock window.alert
    const originalAlert = window.alert
    window.alert = vi.fn()

    wrapper = mount(ApiTokens)
    await flushPromises()

    // Create a new token
    const nameInput = wrapper.find('input#token-name')
    await nameInput.setValue('Clipboard Test Token')
    const createButton = wrapper.find('button.btn-primary')
    await createButton.trigger('click')
    await flushPromises()

    // Find and click copy button
    const copyButton = wrapper.find('button[title="Copy to clipboard"]')
    await copyButton.trigger('click')

    // Verify clipboard was called
    expect(mockWriteText).toHaveBeenCalledWith('new-token-value')
    expect(window.alert).toHaveBeenCalled()

    // Restore original alert
    window.alert = originalAlert
  })

  it('shows loading state', async () => {
    mockTokenStore.loading = true
    
    wrapper = mount(ApiTokens)
    await flushPromises()

    expect(wrapper.find('.loading').exists()).toBe(true)
  })

  it('shows empty state when no tokens', async () => {
    mockTokenStore.tokens = []
    
    wrapper = mount(ApiTokens)
    await flushPromises()

    expect(wrapper.find('.alert.alert-info').exists()).toBe(true)
  })
})

// 🤖 Generated with Claude