<!-- src/components/ServiceCard.vue -->
<template>
  <div class="card h-full bg-base-100 shadow-xl">
    <div class="card-body">
      <h2 class="card-title text-xl">
        <img 
          v-if="customIcon" 
          :src="customIcon" 
          :alt="service.display_name" 
          class="size-[1.5em]"
        />
        <component v-else :is="iconComponent" class="size-[1.5em]" />
        {{ service.display_name }}
        <span v-if="service.is_enabled" class="ml-auto status status-lg" :class="healthStatusClass" :title="healthTooltip"></span>
      </h2>
      
      <!-- GPU Badge - Large and prominent below title -->
      <div v-if="service.gpu_count" class="mb-2">
        <div class="badge badge-lg badge-info gap-2" :title="gpuTooltip">
          <svg xmlns="http://www.w3.org/2000/svg" class="size-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
          </svg>
          <span class="font-semibold">{{ service.gpu_count }} GPU{{ service.gpu_count > 1 ? 's' : '' }}</span>
          <span v-if="service.gpu_nodes && service.gpu_nodes.length > 0" class="text-xs opacity-80">
            ({{ service.gpu_nodes.join(', ') }})
          </span>
        </div>
      </div>
      
      <!-- Description -->
      <p v-if="!compact" class="text-sm opacity-80">{{ service.description }}</p>

      <!-- Service Info -->
      <div v-if="!compact" class="space-x-2">
        <div class="badge badge-sm" :class="typeClass">
          {{ formatServiceType(service.type) }}
        </div>
        <div v-if="service.category" class="badge badge-sm badge-ghost">
          {{ service.category }}
        </div>
      </div>
      
      
      <!-- Actions -->
      <div class="card-actions justify-between mt-auto">
        <!-- Icon actions -->
        <div class="flex gap-1">
          <!-- Open Service -->
          <div v-if="service.is_enabled && isWebUrl(service.url)" class="tooltip" :data-tip="t('dashboard.openService')">
            <a
              :href="service.url"
              target="_blank"
              class="btn btn-primary btn-sm btn-circle"
            >
              <ArrowTopRightOnSquareIcon class="w-4 h-4" />
            </a>
          </div>
          
          <!-- Code Editor for User Apps -->
          <div v-if="service.type === 'user_app'" class="tooltip" :data-tip="t('dashboard.openInCodeEditor')">
            <a 
              :href="codeServerUrl" 
              target="_blank" 
              class="btn btn-accent btn-sm btn-circle"
            >
              <CodeBracketIcon class="w-4 h-4" />
            </a>
          </div>
          
          <!-- Favorite -->
          <div class="tooltip" :data-tip="service.is_favorite ? t('dashboard.removeFromFavorites') : t('dashboard.addToFavorites')">
            <button 
              @click="handleToggleFavorite"
              class="btn btn-ghost btn-sm btn-circle"
            >
              <StarIcon 
                class="w-4 h-4" 
                :class="{ 'text-warning fill-warning': service.is_favorite }" 
              />
            </button>
          </div>
          
          <!-- Details -->
          <div class="tooltip" :data-tip="t('dashboard.viewDetails')">
            <button 
              @click="handleShowDetails"
              class="btn btn-ghost btn-sm btn-circle"
            >
              <InformationCircleIcon class="w-4 h-4" />
            </button>
          </div>
          
          <!-- Restart -->
          <div v-if="service.is_enabled" class="tooltip" :data-tip="t('dashboard.restartService')">
            <button 
              @click="handleRestart"
              class="btn btn-ghost btn-sm btn-circle"
              :disabled="restarting"
            >
              <ArrowPathIcon class="w-4 h-4" :class="{ 'animate-spin': restarting }" />
            </button>
          </div>
          
          <!-- Health Check -->
          <div v-if="service.is_enabled" class="tooltip" :data-tip="t('dashboard.checkHealth')">
            <button
              @click="handleCheckHealth"
              class="btn btn-ghost btn-sm btn-circle"
              :disabled="checkingHealth"
            >
              <HeartIcon class="w-4 h-4" :class="{ 'animate-pulse text-error': checkingHealth }" />
            </button>
          </div>
        </div>

        <!-- Toggle switch for enable/disable -->
        <div v-if="service.can_be_disabled" class="flex items-center gap-2">
          <span class="text-xs" :class="service.is_enabled ? 'text-success' : 'text-base-content opacity-50'">
            {{ service.is_enabled ? 'ON' : 'OFF' }}
          </span>
          <input
            type="checkbox"
            class="toggle toggle-sm"
            :class="service.is_enabled ? 'toggle-success' : ''"
            :checked="service.is_enabled"
            :disabled="toggling"
            @change="toggleService"
          />
          <span v-if="toggling" class="loading loading-spinner loading-xs" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { 
  ServerIcon,
  CodeBracketIcon,
  ChartBarIcon,
  ShieldCheckIcon,
  CircleStackIcon,
  CpuChipIcon,
  DocumentTextIcon,
  CubeIcon,
  Cog6ToothIcon,
  ArrowPathIcon,
  StarIcon,
  InformationCircleIcon,
  HeartIcon,
  ArrowTopRightOnSquareIcon
} from '@heroicons/vue/24/outline'
import { useAuthStore } from '@/stores/auth'
import { useServicesStore } from '@/stores/services'
import { ElMessage } from 'element-plus'

const props = defineProps({
  service: {
    type: Object,
    required: true
  },
  compact: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['toggle', 'restart', 'show-details', 'toggle-favorite'])

const { t } = useI18n()
const authStore = useAuthStore()
const servicesStore = useServicesStore()

const toggling = ref(false)
const restarting = ref(false)
const checkingHealth = ref(false)

// Computed
const healthStatus = computed(() => {
  if (!props.service.is_enabled) return 'disabled'
  if (!props.service.latest_health) return 'unknown'
  return props.service.latest_health.status
})

const healthStatusClass = computed(() => {
  const statusClasses = {
    healthy: 'status-success',
    unhealthy: 'status-error',
    unknown: 'status-neutral',
    disabled: 'status-neutral'
  }
  return statusClasses[healthStatus.value] || 'status-neutral'
})

const healthTooltip = computed(() => {
  if (!props.service.latest_health) return t('dashboard.healthUnknown')
  const lastCheck = new Date(props.service.latest_health.timestamp).toLocaleString()
  return `${healthStatus.value} - ${t('dashboard.lastChecked')}: ${lastCheck}`
})

const gpuTooltip = computed(() => {
  if (!props.service.gpu_nodes || props.service.gpu_nodes.length === 0) {
    return `Using ${props.service.gpu_count} GPU(s)`
  }
  return `Using ${props.service.gpu_count} GPU(s) on node(s): ${props.service.gpu_nodes.join(', ')}`
})

const codeServerUrl = computed(() => {
  // For user apps, construct the code-server URL with the folder parameter
  if (props.service.type === 'user_app' && props.service.url) {
    // Replace the service subdomain with 'code' and add folder parameter
    const serviceUrl = new URL(props.service.url)
    serviceUrl.hostname = serviceUrl.hostname.replace(/^[^.]+/, 'code')
    serviceUrl.searchParams.set('folder', `/home/coder/${props.service.name}`)
    return serviceUrl.toString()
  }
  return null
})


const typeClass = computed(() => {
  const typeClasses = {
    core: 'badge-primary',
    optional: 'badge-secondary',
    user_app: 'badge-accent'
  }
  return typeClasses[props.service.type] || 'badge-ghost'
})

// Track current theme
const currentTheme = ref('thinkube')

// Watch for theme changes
onMounted(() => {
  // Get initial theme
  currentTheme.value = document.documentElement.getAttribute('data-theme') || 'thinkube'
  
  // Watch for theme changes
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.attributeName === 'data-theme') {
        currentTheme.value = document.documentElement.getAttribute('data-theme') || 'thinkube'
      }
    })
  })
  
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme']
  })
})

const customIcon = computed(() => {
  // If icon is already a path, use it directly
  if (props.service.icon && props.service.icon.startsWith('/')) {
    // For dark theme, try to use inverted version if it exists
    if (currentTheme.value === 'thinkube-dark') {
      const iconPath = props.service.icon
      const invertedPath = iconPath.replace('.svg', '_inverted.svg')
      return invertedPath
    }
    return props.service.icon
  }
  
  return null
})

const iconComponent = computed(() => {
  if (props.service.icon && props.service.icon.startsWith('mdi-')) {
    return getIconFromMdi(props.service.icon)
  }
  return getIconFromCategory(props.service.category)
})

// Methods
function isWebUrl(url) {
  if (!url) return false
  // Only show open button for HTTP/HTTPS URLs that are not internal cluster URLs
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    return false
  }
  // Check if it's an internal cluster URL (not accessible from browser)
  if (url.includes('.svc.cluster.local') || url.includes('internal')) {
    return false
  }
  return true
}

function formatServiceType(type) {
  const typeLabels = {
    'core': 'Core',
    'optional': 'Optional',
    'user_app': 'User App'
  }
  return typeLabels[type] || type
}

function getIconFromMdi(mdiIcon) {
  const iconMap = {
    'mdi-server': ServerIcon,
    'mdi-code-braces': CodeBracketIcon,
    'mdi-chart-line': ChartBarIcon,
    'mdi-shield-check': ShieldCheckIcon,
    'mdi-database': CircleStackIcon,
    'mdi-brain': CpuChipIcon,
    'mdi-book-open': DocumentTextIcon,
    'mdi-application': CubeIcon,
    'mdi-docker': CubeIcon,
    'mdi-git': CodeBracketIcon,
    'mdi-sync-circle': ArrowPathIcon,
    'mdi-cog': Cog6ToothIcon
  }
  return iconMap[mdiIcon] || ServerIcon
}

function getIconFromCategory(category) {
  const categoryMap = {
    'infrastructure': ServerIcon,
    'development': CodeBracketIcon,
    'monitoring': ChartBarIcon,
    'security': ShieldCheckIcon,
    'storage': CircleStackIcon,
    'ai': CpuChipIcon,
    'documentation': DocumentTextIcon,
    'application': CubeIcon
  }
  return categoryMap[category] || ServerIcon
}

async function toggleService() {
  toggling.value = true
  try {
    await emit('toggle', props.service, !props.service.is_enabled)
  } finally {
    toggling.value = false
  }
}

async function checkHealth() {
  checkingHealth.value = true
  try {
    await servicesStore.triggerHealthCheck(props.service.id)
    ElMessage.success(t('dashboard.healthCheckComplete'))
  } catch (error) {
    ElMessage.error(t('dashboard.healthCheckError'))
  } finally {
    checkingHealth.value = false
  }
}

function handleToggleFavorite() {
  emit('toggle-favorite', props.service)
}

function handleShowDetails() {
  emit('show-details', props.service)
}

async function handleRestart() {
  restarting.value = true
  try {
    await emit('restart', props.service)
  } finally {
    // Reset after a delay to show the animation
    setTimeout(() => {
      restarting.value = false
    }, 1000)
  }
}

async function handleCheckHealth() {
  await checkHealth()
}
</script>