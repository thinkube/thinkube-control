// src/views/__tests__/Login.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import Login from '../Login.vue'
import * as authService from '@/services/auth'

// Mock the auth service
vi.mock('@/services/auth', () => ({
  redirectToLogin: vi.fn()
}))


// Mock window.location
delete window.location
window.location = { 
  href: vi.fn(),
  origin: 'http://localhost:3000'
}

describe('Login.vue', () => {
  let wrapper

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the login page with correct elements', () => {
    wrapper = mount(Login)
    
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('h2').exists()).toBe(true)
    expect(wrapper.find('p').exists()).toBe(true)
    expect(wrapper.find('button').exists()).toBe(true)
  })

  it('shows loading state when login is clicked', async () => {
    // Make redirectToLogin return a promise that doesn't resolve immediately
    authService.redirectToLogin.mockImplementation(() => new Promise(() => {}))
    
    wrapper = mount(Login)
    const loginButton = wrapper.find('button')
    
    expect(loginButton.attributes('disabled')).toBeUndefined()
    
    await loginButton.trigger('click')
    
    expect(loginButton.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.loading').exists()).toBe(true)
  })

  it('calls auth service redirectToLogin when button is clicked', async () => {
    authService.redirectToLogin.mockResolvedValue(undefined)
    
    wrapper = mount(Login)
    const loginButton = wrapper.find('button')
    
    await loginButton.trigger('click')
    
    expect(authService.redirectToLogin).toHaveBeenCalled()
  })

  it('shows error message when login fails', async () => {
    const errorMessage = 'Authentication failed'
    authService.redirectToLogin.mockRejectedValue(new Error(errorMessage))
    
    wrapper = mount(Login)
    const loginButton = wrapper.find('button')
    
    await loginButton.trigger('click')
    await wrapper.vm.$nextTick()
    
    expect(wrapper.find('.alert-error').exists()).toBe(true)
    expect(wrapper.find('.alert-error').text()).toContain(errorMessage)
  })

  it('shows default error message when error has no message', async () => {
    authService.redirectToLogin.mockRejectedValue(new Error())
    
    wrapper = mount(Login)
    const loginButton = wrapper.find('button')
    
    await loginButton.trigger('click')
    await wrapper.vm.$nextTick()
    
    expect(wrapper.find('.alert-error').exists()).toBe(true)
  })

  it('does not show error alert initially', () => {
    wrapper = mount(Login)
    
    expect(wrapper.find('.alert-error').exists()).toBe(false)
  })

  it('clears error when trying to login again', async () => {
    authService.redirectToLogin.mockRejectedValueOnce(new Error('First error'))
                               .mockImplementation(() => new Promise(() => {}))
    
    wrapper = mount(Login)
    const loginButton = wrapper.find('button')
    
    // First click - fail
    await loginButton.trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.alert-error').exists()).toBe(true)
    
    // Second click - should clear error
    await loginButton.trigger('click')
    expect(wrapper.find('.alert-error').exists()).toBe(false)
  })
})

// 🤖 Generated with Claude