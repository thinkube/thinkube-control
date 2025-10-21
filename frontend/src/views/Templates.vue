<!-- src/views/Templates.vue -->
<template>
  <div class="container">
    <div class="prose prose-lg">
      <h1>{{ t('templates.title') }}</h1>
      <p class="lead">
        {{ t('templates.subtitle') }}
      </p>
    </div>
    
    <!-- Template Deployment Form -->
    <div
      v-if="showDeployForm"
      class="card"
    >
      <div class="card-body">
        <h2 class="card-title">
          Deploy Template
        </h2>
        
        <!-- Template Info -->
        <div
          v-if="templateInfo"
          class="alert alert-info alert-soft"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            class="stroke-current shrink-0 icon-md"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <div>
            <h3 class="font-bold">
              {{ templateInfo.name }}
            </h3>
            <p>{{ templateInfo.description }}</p>
            <a
              :href="templateUrl"
              target="_blank"
              class="link link-primary text-sm"
            >
              {{ templateUrl }}
            </a>
          </div>
        </div>
        
        <!-- Loading template metadata -->
        <div
          v-if="loadingMetadata"
          class="hero"
        >
          <div class="hero-content">
            <div>
              <span class="loading loading-spinner loading-lg" />
              <p>Loading template configuration...</p>
            </div>
          </div>
        </div>
        
        <!-- Dynamic form based on template.yaml -->
        <TemplateParameterForm 
          v-else-if="templateMetadata"
          v-model="deployConfig"
          :parameters="templateMetadata.parameters"
        />
        
        <!-- No template.yaml found -->
        <div
          v-else
          class="alert alert-error"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            class="h-6 w-6 shrink-0 stroke-current"
            fill="none"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <div>
            <h3 class="font-bold">
              Invalid Template
            </h3>
            <p>This template does not have a template.yaml file.</p>
            <p>All Thinkube templates must include a template.yaml manifest file.</p>
          </div>
        </div>
        
        <!-- Action Buttons -->
        <div class="card-actions justify-end">
          <button
            class="btn btn-ghost"
            @click="cancelDeploy"
          >
            Cancel
          </button>
          <button 
            class="btn btn-primary" 
            :disabled="!isValidConfig || isDeploying"
            @click="handleDeployTemplate"
          >
            <span
              v-if="isDeploying"
              class="loading loading-spinner"
            />
            {{ isDeploying ? 'Deploying...' : 'Deploy Template' }}
          </button>
        </div>
      </div>
    </div>
    
    <!-- Playbook Executor -->
    <PlaybookExecutor
      ref="playbookExecutor"
      :title="`Deploying ${deployConfig.project_name}`"
      :success-message="`Deployment complete! Your application will be available at https://${deployConfig.project_name}.${domainName}`"
      :on-complete="handleDeploymentComplete"
    />
    
    <!-- Manual Template URL -->
    <div
      v-if="!showDeployForm"
      class="card"
    >
      <div class="card-body">
        <h2 class="card-title">
          Deploy from GitHub
        </h2>
        <p>Enter a GitHub repository URL to deploy a template</p>
        
        <fieldset class="fieldset">
          <label
            class="fieldset-label"
            for="template-url"
          >
            Template Repository URL
          </label>
          <input 
            id="template-url"
            v-model="manualTemplateUrl" 
            type="url" 
            placeholder="https://github.com/thinkube/tkt-webapp-vue-fastapi"
            class="input"
          >
        </fieldset>
        
        <div class="card-actions justify-end">
          <button 
            class="btn btn-primary" 
            :disabled="!isValidUrl(manualTemplateUrl)"
            @click="loadTemplate"
          >
            Load Template
          </button>
        </div>
      </div>
    </div>
    
    <!-- Available Templates -->
    <div class="mb-4">
      <h2 class="text-2xl font-bold mb-4">
        Available Templates
      </h2>
      
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <!-- Vue + FastAPI Template -->
          <div class="flex">
            <div class="card h-full bg-base-100 shadow-xl w-full">
              <div class="card-body flex flex-col">
                <h2 class="card-title">
                  Vue.js + FastAPI
                </h2>
                <p class="text-sm opacity-80">Full-stack web application with authentication and i18n</p>
                <div class="space-x-2 mb-4">
                  <span class="badge badge-sm badge-primary">Vue.js 3</span>
                  <span class="badge badge-sm badge-primary">FastAPI</span>
                  <span class="badge badge-sm badge-warning">Keycloak</span>
                  <span class="badge badge-sm badge-info">PostgreSQL</span>
                </div>
                <div class="card-actions justify-end mt-auto">
                  <button 
                    class="btn btn-primary btn-sm"
                    @click="selectTemplate('https://github.com/thinkube/tkt-webapp-vue-fastapi')"
                  >
                    Deploy
                  </button>
                </div>
              </div>
            </div>
          </div>
          
          <!-- AI Model Inference Server -->
          <div class="flex">
            <div class="card h-full bg-base-100 shadow-xl w-full">
              <div class="card-body flex flex-col">
                <h2 class="card-title">
                  vLLM Server
                </h2>
                <p class="text-sm opacity-80">High-performance LLM inference (requires RTX 3090+)</p>
                <div class="space-x-2 mb-4">
                  <span class="badge badge-sm badge-primary">Gradio</span>
                  <span class="badge badge-sm badge-primary">FastAPI</span>
                  <span class="badge badge-sm badge-success">GPU</span>
                  <span class="badge badge-sm badge-info">HuggingFace</span>
                </div>
                <div class="card-actions justify-end mt-auto">
                  <button 
                    class="btn btn-primary btn-sm"
                    @click="selectTemplate('https://github.com/thinkube/tkt-vllm-gradio')"
                  >
                    Deploy
                  </button>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Stable Diffusion Template -->
          <div class="flex">
            <div class="card h-full bg-base-100 shadow-xl w-full">
              <div class="card-body flex flex-col">
                <h2 class="card-title">
                  Stable Diffusion
                </h2>
                <p class="text-sm opacity-80">AI image generation with SDXL and SD 1.5 models</p>
                <div class="space-x-2 mb-4">
                  <span class="badge badge-sm badge-primary">Diffusers</span>
                  <span class="badge badge-sm badge-primary">Gradio</span>
                  <span class="badge badge-sm badge-success">GPU</span>
                  <span class="badge badge-sm badge-info">HuggingFace</span>
                </div>
                <div class="card-actions justify-end mt-auto">
                  <button 
                    class="btn btn-primary btn-sm"
                    @click="selectTemplate('https://github.com/thinkube/tkt-stable-diffusion')"
                  >
                    Deploy
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
    </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { deployTemplateAsync } from '@/services/api'
import TemplateParameterForm from '@/components/TemplateParameterForm.vue'
import PlaybookExecutor from '@/components/PlaybookExecutor.vue'
import { api } from '@/services/api'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// Reactive state
const showDeployForm = ref(false)
const templateUrl = ref('')
const templateInfo = ref(null)
const templateMetadata = ref(null)
const loadingMetadata = ref(false)
const manualTemplateUrl = ref('')
const isDeploying = ref(false)
const deploymentId = ref(null)
const deploymentFailed = ref(false)

// Deployment configuration
const deployConfig = ref({
  project_name: '',
  project_description: ''
})

// Reference to PlaybookExecutor component
const playbookExecutor = ref(null)

// Computed properties
const isValidConfig = computed(() => {
  // Only allow deployment if we have valid template metadata
  return templateMetadata.value && 
         deployConfig.value.project_name && 
         /^[a-z][a-z0-9-]*$/.test(deployConfig.value.project_name)
})

// Compute domain name to avoid direct window access in template
const domainName = computed(() => {
  if (typeof window === 'undefined') return 'thinkube.com'
  return window.location.hostname.replace('control.', '')
})

const isValidUrl = (url) => {
  try {
    const u = new URL(url)
    return u.hostname === 'github.com' && u.pathname.split('/').length >= 3
  } catch {
    return false
  }
}

// Check for deploy parameter on mount
onMounted(() => {
  const deployUrl = route.query.deploy
  if (deployUrl) {
    templateUrl.value = deployUrl
    loadTemplate()
  }
})

// Load template information
const loadTemplate = async () => {
  const url = templateUrl.value || manualTemplateUrl.value
  if (!isValidUrl(url)) {
    alert('Please enter a valid GitHub repository URL')
    return
  }
  
  templateUrl.value = url
  showDeployForm.value = true
  loadingMetadata.value = true
  templateMetadata.value = null
  
  // Extract repo info from URL
  const parts = url.split('/')
  const owner = parts[3]
  const repo = parts[4]
  
  templateInfo.value = {
    name: repo,
    description: 'Loading template information...',
    owner: owner
  }
  
  // Try to fetch template metadata from our API
  try {
    const token = localStorage.getItem('access_token')
    const response = await api.get('/templates/metadata', {
      params: { template_url: url },
      headers: { Authorization: `Bearer ${token}` }
    })
    
    if (response.data) {
      templateMetadata.value = response.data
      templateInfo.value.description = response.data.metadata.description || 'Template ready'
      
      // Initialize deployConfig with any defaults from parameters
      const defaultValues = {}
      response.data.parameters.forEach(param => {
        if (param.default !== undefined && param.default !== null) {
          defaultValues[param.name] = param.default
        }
      })
      
      deployConfig.value = {
        project_name: '',
        project_description: '',
        ...defaultValues,
        ...deployConfig.value
      }
    }
  } catch (e) {
    console.error('Failed to fetch template metadata:', e)
    // No template.yaml found - this is an error
    templateInfo.value.description = 'Invalid template - missing template.yaml'
    templateMetadata.value = null
  } finally {
    loadingMetadata.value = false
  }
  
  // Clear query parameter
  if (route.query.deploy) {
    router.replace({ query: {} })
  }
}

// Select a template from the gallery
const selectTemplate = (url) => {
  templateUrl.value = url
  loadTemplate()
}

// Cancel deployment
const cancelDeploy = () => {
  showDeployForm.value = false
  templateUrl.value = ''
  templateInfo.value = null
  templateMetadata.value = null
  loadingMetadata.value = false
  deploymentFailed.value = false
  deployConfig.value = {
    project_name: '',
    project_description: ''
  }
}

// Handle deployment completion
const handleDeploymentComplete = (result) => {
  isDeploying.value = false
  
  if (result.status === 'success') {
    // Show success notification or redirect
    console.log('Deployment completed successfully')
  } else if (result.status === 'error') {
    deploymentFailed.value = true
    console.error('Deployment failed:', result.message)
  }
}

// Deploy the template
const handleDeployTemplate = async () => {
  if (!isValidConfig.value || isDeploying.value) return
  
  isDeploying.value = true
  deploymentFailed.value = false
  
  try {
    // Deploy template asynchronously
    const response = await deployTemplateAsync({
      template_url: templateUrl.value,
      template_name: deployConfig.value.project_name,
      variables: {
        ...deployConfig.value,
        domain_name: domainName.value,
        author_name: 'Thinkube User',
        author_email: `user@${domainName.value}`
      }
    })
    
    // Check if there's a conflict that requires confirmation
    if (response.status === 'conflict' && response.requires_confirmation) {
      isDeploying.value = false
      
      // Show confirmation dialog
      const confirmed = confirm(
        `${response.message}\n\nDo you want to overwrite the existing application?`
      )
      
      if (confirmed) {
        // Retry with overwrite flag
        isDeploying.value = true
        const retryResponse = await deployTemplateAsync({
          template_url: templateUrl.value,
          template_name: deployConfig.value.project_name,
          variables: {
            ...deployConfig.value,
            domain_name: domainName.value,
            author_name: 'Thinkube User',
            author_email: `user@${domainName.value}`,
            _overwrite_confirmed: true
          }
        })
        
        deploymentId.value = retryResponse.deployment_id
        
        // Start PlaybookExecutor with WebSocket URL
        if (retryResponse.websocket_url) {
          playbookExecutor.value?.startExecution(`/api/v1${retryResponse.websocket_url}`)
        } else {
          playbookExecutor.value?.startExecution(`/api/v1/ws/deployment/${retryResponse.deployment_id}`)
        }
      } else {
        // User cancelled
        console.log('Deployment cancelled by user')
      }
      return
    }
    
    deploymentId.value = response.deployment_id
    
    // Start PlaybookExecutor with WebSocket URL
    if (response.websocket_url) {
      playbookExecutor.value?.startExecution(`/api/v1${response.websocket_url}`)
    } else {
      playbookExecutor.value?.startExecution(`/api/v1/ws/deployment/${response.deployment_id}`)
    }
    
  } catch (error) {
    console.error('Failed to deploy template:', error)
    alert(`Failed to deploy template: ${error.response?.data?.detail || error.message}`)
    isDeploying.value = false
  }
}

// Cleanup on unmount
onUnmounted(() => {
  // No WebSocket to cleanup anymore, handled by PlaybookExecutor
})
</script>

<style scoped>
/* Terminal output styling optimized for dark backgrounds */
.mockup-code {
  background-color: #1a1a1a !important;
  color: #e0e0e0 !important;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

/* Terminal output colors optimized for dark backgrounds */
.terminal-task {
  color: #60a5fa !important; /* Bright blue for tasks */
  font-weight: bold;
}

.terminal-success {
  color: #4ade80 !important; /* Bright green for success */
}

.terminal-changed {
  color: #fbbf24 !important; /* Bright yellow for changes */
}

.terminal-error {
  color: #f87171 !important; /* Bright red for errors */
  font-weight: bold;
}

.terminal-play {
  color: #38bdf8 !important; /* Bright cyan for plays */
  font-weight: bold;
  margin-top: 0.5rem;
}

.terminal-complete {
  color: #fb923c !important; /* Bright orange for completion */
  font-weight: bold;
  margin-top: 0.5rem;
}

.terminal-skipped {
  color: #9ca3af !important; /* Gray for skipped */
}

.terminal-output {
  color: #e0e0e0 !important; /* Light gray for regular output */
}

.output-line-spaced {
  margin-top: 0.5rem;
}
</style>