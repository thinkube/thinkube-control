import { config } from '@vue/test-utils'
import { vi } from 'vitest'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key) => key,
    locale: { value: 'en' }
  }),
  createI18n: () => ({
    install: () => {}
  })
}))

// Configure test utils global mocks
config.global.mocks = {
  $t: (key) => key,
  t: (key) => key
}