<!-- src/components/FavoriteServiceCard.vue -->
<template>
  <div class="card h-full bg-base-100 shadow-xl card-compact">
    <div class="card-body">
      <h2 class="card-title text-base">
        <svg 
          class="drag-handle size-[1em] cursor-move mr-1 opacity-50 hover:opacity-100"
          xmlns="http://www.w3.org/2000/svg" 
          fill="none" 
          viewBox="0 0 24 24" 
          stroke="currentColor"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
        <img 
          v-if="customIcon" 
          :src="customIcon" 
          :alt="service.display_name" 
          class="size-[1.5em]"
        />
        <component v-else :is="iconComponent" class="size-[1.5em]" />
        {{ service.display_name }}
        <button
          class="btn btn-ghost btn-xs btn-square ml-auto"
          @click="$emit('toggle-favorite', service)"
          :title="t('dashboard.removeFromFavorites')"
        >
          <StarIcon class="w-4 h-4 text-warning" />
        </button>
      </h2>
      
      <!-- GPU Badge - Larger and more prominent -->
      <div v-if="service.gpu_count" class="mb-1">
        <div class="badge badge-md badge-info gap-1" :title="gpuTooltip">
          <svg xmlns="http://www.w3.org/2000/svg" class="size-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
          </svg>
          <span class="font-semibold">{{ service.gpu_count }} GPU{{ service.gpu_count > 1 ? 's' : '' }}</span>
        </div>
      </div>
      
      <div class="flex items-center gap-2">
        <span v-if="service.is_enabled" class="status status-lg" :class="healthStatusClass"></span>
      </div>
      
      <div class="card-actions justify-end mt-auto">
        <div class="flex gap-1">
          <!-- Open Service -->
          <div v-if="service.is_enabled && isWebUrl(service.url)" class="tooltip" :data-tip="t('dashboard.openService')">
            <a
              :href="service.url"
              target="_blank"
              class="btn btn-primary btn-xs btn-circle"
            >
              <ArrowTopRightOnSquareIcon class="w-3 h-3" />
            </a>
          </div>
          
          <!-- Details -->
          <div class="tooltip" :data-tip="t('dashboard.viewDetails')">
            <button
              class="btn btn-ghost btn-xs btn-circle"
              @click="$emit('show-details', service)"
            >
              <InformationCircleIcon class="w-3 h-3" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { 
  InformationCircleIcon,
  StarIcon,
  ArrowTopRightOnSquareIcon,
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
  BeakerIcon,
  CommandLineIcon
} from '@heroicons/vue/24/outline'

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

const emit = defineEmits(['toggle-favorite', 'show-details'])
const { t } = useI18n()

// Health status computed properties
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

const gpuTooltip = computed(() => {
  if (!props.service.gpu_nodes || props.service.gpu_nodes.length === 0) {
    return `Using ${props.service.gpu_count} GPU(s)`
  }
  return `Using ${props.service.gpu_count} GPU(s) on node(s): ${props.service.gpu_nodes.join(', ')}`
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
    'mdi-cog': Cog6ToothIcon,
    'mdi-beaker': BeakerIcon
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
    'application': CubeIcon,
    'keycloak': BeakerIcon,
    'gitea': CommandLineIcon,
    'postgresql': CircleStackIcon
  }
  return categoryMap[category?.toLowerCase()] || categoryMap[props.service.name] || ServerIcon
}
</script>