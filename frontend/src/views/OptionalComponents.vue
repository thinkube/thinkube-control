<!-- src/views/OptionalComponents.vue -->
<template>
  <div class="container">
    <div class="prose prose-lg">
      <h1>{{ t('optionalComponents.title') }}</h1>
      <p class="lead">
        {{ t('optionalComponents.subtitle') }}
      </p>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="flex justify-center py-8">
      <span class="loading loading-spinner loading-lg" />
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="alert alert-error">
      <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 shrink-0 stroke-current" fill="none" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>{{ error }}</span>
    </div>

    <!-- Component Categories -->
    <div v-else class="space-y-8">
      <!-- AI Components -->
      <div v-if="aiComponents.length > 0">
        <h2 class="text-2xl font-bold mb-4">AI & Machine Learning</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <ComponentCard 
            v-for="component in aiComponents" 
            :key="component.name"
            :component="component"
            @install="handleInstall"
            @uninstall="handleUninstall"
          />
        </div>
      </div>

      <!-- Data Components -->
      <div v-if="dataComponents.length > 0">
        <h2 class="text-2xl font-bold mb-4">Data & Storage</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <ComponentCard 
            v-for="component in dataComponents" 
            :key="component.name"
            :component="component"
            @install="handleInstall"
            @uninstall="handleUninstall"
          />
        </div>
      </div>

      <!-- Monitoring Components -->
      <div v-if="monitoringComponents.length > 0">
        <h2 class="text-2xl font-bold mb-4">Monitoring & Observability</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <ComponentCard 
            v-for="component in monitoringComponents" 
            :key="component.name"
            :component="component"
            @install="handleInstall"
            @uninstall="handleUninstall"
          />
        </div>
      </div>

      <!-- Infrastructure Components -->
      <div v-if="infrastructureComponents.length > 0">
        <h2 class="text-2xl font-bold mb-4">Infrastructure & Platform</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <ComponentCard 
            v-for="component in infrastructureComponents" 
            :key="component.name"
            :component="component"
            @install="handleInstall"
            @uninstall="handleUninstall"
          />
        </div>
      </div>
    </div>

    <!-- Playbook Executor -->
    <PlaybookExecutor
      ref="playbookExecutor"
      :title="installingTitle"
      :success-message="installingSuccessMessage"
      :on-complete="handleInstallationComplete"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import ComponentCard from '@/components/ComponentCard.vue'
import PlaybookExecutor from '@/components/PlaybookExecutor.vue'
import { useOptionalComponentsStore } from '@/stores/optionalComponents'

const { t } = useI18n()
const store = useOptionalComponentsStore()

const loading = ref(false)
const error = ref(null)
const components = ref([])
const installingComponent = ref(null)
const installingTitle = ref('')
const installingSuccessMessage = ref('')
const playbookExecutor = ref(null)

// Computed properties for component categories
const aiComponents = computed(() => 
  components.value.filter(c => c.category === 'ai')
)

const dataComponents = computed(() => 
  components.value.filter(c => c.category === 'data')
)

const monitoringComponents = computed(() => 
  components.value.filter(c => c.category === 'monitoring')
)

const infrastructureComponents = computed(() => 
  components.value.filter(c => c.category === 'infrastructure')
)

// Load components on mount
onMounted(async () => {
  await loadComponents()
})

async function loadComponents() {
  loading.value = true
  error.value = null
  
  try {
    const response = await store.listComponents()
    components.value = response.components
  } catch (err) {
    console.error('Failed to load optional components:', err)
    error.value = 'Failed to load optional components. Please try again.'
    alert('Failed to load optional components')
  } finally {
    loading.value = false
  }
}

async function handleInstall(component) {
  try {
    // Set installation info
    installingComponent.value = component
    installingTitle.value = `Installing ${component.display_name}`
    installingSuccessMessage.value = `${component.display_name} has been installed successfully!`
    
    // Start installation
    const response = await store.installComponent(component.name, {})
    
    // Start PlaybookExecutor with WebSocket URL
    const wsPath = `/api/v1/ws/optional/${component.name}/install/${response.deployment_id}`
    playbookExecutor.value?.startExecution(wsPath)
    
  } catch (err) {
    console.error('Failed to install component:', err)
    alert(`Failed to install ${component.display_name}: ${err.message}`)
  }
}

async function handleUninstall(component) {
  if (!confirm(`Are you sure you want to uninstall ${component.display_name}?`)) {
    return
  }
  
  try {
    // Set uninstall info
    installingComponent.value = component
    installingTitle.value = `Uninstalling ${component.display_name}`
    installingSuccessMessage.value = `${component.display_name} has been uninstalled successfully!`
    
    const response = await store.uninstallComponent(component.name)
    
    // Start PlaybookExecutor with WebSocket URL for uninstall
    const wsPath = `/api/v1/ws/optional/${component.name}/uninstall/${response.deployment_id}`
    playbookExecutor.value?.startExecution(wsPath)
    
  } catch (err) {
    console.error('Failed to uninstall component:', err)
    alert(`Failed to uninstall ${component.display_name}: ${err.message}`)
  }
}

function handleInstallationComplete(result) {
  if (result.status === 'success') {
    console.log(`${installingComponent.value?.display_name} operation completed successfully`)

    // Reset state only on success
    installingComponent.value = null
    installingTitle.value = ''
    installingSuccessMessage.value = ''

    // Reload components to update status
    loadComponents()
  } else if (result.status === 'error') {
    console.error(`${installingComponent.value?.display_name} operation failed:`, result.message)
    // Keep the state on error - user must manually close the modal
    // This allows them to read the logs and retry if needed
  }
}
</script>