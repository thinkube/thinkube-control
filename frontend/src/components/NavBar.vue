<!-- src/components/NavBar.vue -->
<template>
  <div class="navbar bg-base-100 shadow-md sticky top-0 z-50">
    <div class="navbar-start">
      <div class="dropdown">
        <label
          tabindex="0"
          class="btn btn-ghost lg:hidden"
        >
          <Bars3Icon class="icon-md" />
        </label>
        <ul
          tabindex="0"
          class="menu menu-sm dropdown-content shadow bg-base-100 rounded-box z-50 w-56"
        >
          <!-- Dashboard -->
          <li>
            <router-link to="/">
              {{ t('nav.dashboard') }}
            </router-link>
          </li>

          <!-- Deployment & Infrastructure Submenu -->
          <li>
            <details>
              <summary>{{ t('nav.groups.deployment') }}</summary>
              <ul>
                <li>
                  <router-link to="/templates">
                    {{ t('nav.templates') }}
                  </router-link>
                </li>
                <li>
                  <router-link to="/harbor-images">
                    {{ t('nav.harborImages') }}
                  </router-link>
                </li>
                <li>
                  <router-link to="/optional-components">
                    {{ t('nav.optionalComponents') }}
                  </router-link>
                </li>
              </ul>
            </details>
          </li>

          <!-- Configuration & Security Submenu -->
          <li>
            <details>
              <summary>{{ t('nav.groups.config') }}</summary>
              <ul>
                <li>
                  <router-link to="/jupyterhub-config">
                    {{ t('nav.jupyterHubConfig') }}
                  </router-link>
                </li>
                <li>
                  <router-link to="/secrets">
                    {{ t('nav.secrets') }}
                  </router-link>
                </li>
                <li>
                  <router-link to="/tokens">
                    {{ t('nav.apiTokens') }}
                  </router-link>
                </li>
              </ul>
            </details>
          </li>
        </ul>
      </div>
      <router-link
        to="/"
        class="btn btn-ghost p-1 flex items-center gap-2"
      >
        <img 
          :src="logoSrc" 
          alt="Thinkube"
          class="h-10"
        />
        <span class="text-xl font-semibold">{{ t('app.title') }}</span>
      </router-link>
    </div>
    
    <div class="navbar-center hidden lg:flex">
      <ul class="menu menu-horizontal">
        <!-- Dashboard -->
        <li>
          <router-link to="/">
            {{ t('nav.dashboard') }}
          </router-link>
        </li>

        <!-- Deployment & Infrastructure Dropdown -->
        <li>
          <details>
            <summary>{{ t('nav.groups.deployment') }}</summary>
            <ul class="bg-base-100 rounded-box z-50 w-56">
              <li>
                <router-link to="/templates">
                  {{ t('nav.templates') }}
                </router-link>
              </li>
              <li>
                <router-link to="/harbor-images">
                  {{ t('nav.harborImages') }}
                </router-link>
              </li>
              <li>
                <router-link to="/optional-components">
                  {{ t('nav.optionalComponents') }}
                </router-link>
              </li>
            </ul>
          </details>
        </li>

        <!-- Configuration & Security Dropdown -->
        <li>
          <details>
            <summary>{{ t('nav.groups.config') }}</summary>
            <ul class="bg-base-100 rounded-box z-50 w-56">
              <li>
                <router-link to="/jupyterhub-config">
                  {{ t('nav.jupyterHubConfig') }}
                </router-link>
              </li>
              <li>
                <router-link to="/secrets">
                  {{ t('nav.secrets') }}
                </router-link>
              </li>
              <li>
                <router-link to="/tokens">
                  {{ t('nav.apiTokens') }}
                </router-link>
              </li>
            </ul>
          </details>
        </li>
      </ul>
    </div>
    
    <div class="navbar-end">
      <!-- Theme switcher toggle -->
      <label class="swap swap-rotate btn btn-ghost btn-sm btn-circle">
        <input
          type="checkbox"
          class="theme-controller"
          :checked="currentTheme === 'thinkube-dark'"
          @change="toggleTheme"
        />
        <SunIcon class="swap-on w-5 h-5" />
        <MoonIcon class="swap-off w-5 h-5" />
      </label>
      
      <!-- Language selector -->
      <div class="dropdown dropdown-end">
        <label
          tabindex="0"
          class="btn btn-ghost btn-sm"
        >
          <LanguageIcon class="icon-md" />
          <span>{{ currentLocale.toUpperCase() }}</span>
        </label>
        <ul
          tabindex="0"
          class="menu menu-sm dropdown-content shadow bg-base-100 rounded-box"
        >
          <li><a @click="changeLocale('en')">English</a></li>
          <li><a @click="changeLocale('es')">Español</a></li>
          <li><a @click="changeLocale('ca')">Català</a></li>
          <li><a @click="changeLocale('fr')">Français</a></li>
          <li><a @click="changeLocale('it')">Italiano</a></li>
          <li><a @click="changeLocale('de')">Deutsch</a></li>
        </ul>
      </div>
      
      <ProfileDropdown
        :user="user"
        @logout="logout"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bars3Icon, LanguageIcon, SunIcon, MoonIcon } from '@heroicons/vue/24/outline'
import ProfileDropdown from './ProfileDropdown.vue'
import { logout } from '@/services/auth'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

const { t, locale } = useI18n()
const currentLocale = locale

const currentTheme = ref('thinkube')

// Computed property for logo based on theme
const logoSrc = computed(() => {
  return currentTheme.value === 'thinkube-dark' 
    ? '/icons/tk_logo_inverted.svg'
    : '/icons/tk_logo.svg'
})

const setTheme = (theme) => {
  currentTheme.value = theme
  document.documentElement.setAttribute('data-theme', theme)
  localStorage.setItem('theme', theme)
}

const toggleTheme = () => {
  const newTheme = currentTheme.value === 'thinkube' ? 'thinkube-dark' : 'thinkube'
  setTheme(newTheme)
}

// Initialize theme state on component mount
onMounted(() => {
  const savedTheme = localStorage.getItem('theme') || 'thinkube'
  setTheme(savedTheme)
})

const changeLocale = (newLocale) => {
  locale.value = newLocale
  localStorage.setItem('locale', newLocale)
}
</script>