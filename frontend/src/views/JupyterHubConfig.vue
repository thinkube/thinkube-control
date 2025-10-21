<template>
  <div class="container mx-auto p-6 max-w-6xl">
    <h1 class="font-bold mb-2">{{ $t('jupyterHubConfig.title') }}</h1>
    <p class="text-base-content/70 mb-6">{{ $t('jupyterHubConfig.description') }}</p>

    <!-- Loading State -->
    <div v-if="loading" class="flex justify-center items-center py-12">
      <span class="loading loading-spinner loading-lg"></span>
    </div>

    <!-- Error Alert -->
    <div v-if="error" class="alert alert-error mb-6">
      <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 shrink-0 stroke-current" fill="none" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>{{ error }}</span>
    </div>

    <!-- Success Alert -->
    <div v-if="saveSuccess" class="alert alert-success mb-6">
      <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 shrink-0 stroke-current" fill="none" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>{{ $t('jupyterHubConfig.saved') }}</span>
    </div>

    <!-- Configuration Form -->
    <div v-if="!loading" class="space-y-6">
      <!-- Image Selection Section -->
      <div class="card bg-base-200">
        <div class="card-body">
          <h2 class="card-title">{{ $t('jupyterHubConfig.imageSelection') }}</h2>
          <p class="text-sm text-base-content/70 mb-4">{{ $t('jupyterHubConfig.imageSelectionDesc') }}</p>

          <div v-if="availableImages.length === 0" class="alert alert-warning">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 shrink-0 stroke-current" fill="none" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span>{{ $t('jupyterHubConfig.noImagesFound') }}</span>
          </div>

          <div v-else class="space-y-3">
            <div v-for="image in availableImages" :key="image.name" class="flex items-center gap-4 p-3 bg-base-100 rounded-lg">
              <input
                type="checkbox"
                :id="`hide-${image.name}`"
                :checked="config.hidden_images.includes(image.name)"
                @change="toggleImageVisibility(image.name)"
                class="checkbox checkbox-sm"
              />
              <label :for="`hide-${image.name}`" class="flex-1 cursor-pointer">
                <div class="font-medium">{{ image.display_name }}</div>
                <div class="text-sm text-base-content/60">{{ image.description }}</div>
                <div class="text-xs text-base-content/40 font-mono">{{ image.name }}</div>
              </label>
              <button
                @click="setDefaultImage(image.name)"
                :disabled="config.hidden_images.includes(image.name)"
                :class="['btn btn-sm', config.default_image === image.name ? 'btn-primary' : 'btn-ghost']"
              >
                <span v-if="config.default_image === image.name">⭐ {{ $t('jupyterHubConfig.default') }}</span>
                <span v-else>{{ $t('jupyterHubConfig.setDefault') }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Default Resources Section -->
      <div class="card bg-base-200">
        <div class="card-body">
          <h2 class="card-title">{{ $t('jupyterHubConfig.defaultResources') }}</h2>
          <p class="text-sm text-base-content/70 mb-4">{{ $t('jupyterHubConfig.defaultResourcesDesc') }}</p>

          <!-- Node Selection -->
          <fieldset class="fieldset mb-4">
            <legend class="fieldset-legend">{{ $t('jupyterHubConfig.defaultNode') }}</legend>
            <select
              v-model="config.default_node"
              class="select select-bordered w-full"
              @change="onNodeChange"
            >
              <option :value="null">{{ $t('jupyterHubConfig.selectNode') }}</option>
              <option v-for="node in clusterNodes" :key="node.name" :value="node.name">
                {{ node.name }} ({{ node.capacity.cpu }} cores, {{ node.capacity.memory }}, {{ node.capacity.gpu }} GPUs)
              </option>
            </select>
          </fieldset>

          <!-- Resource Sliders -->
          <div v-if="selectedNodeCapacity" class="space-y-8">
            <!-- CPU -->
            <div class="w-full">
              <label class="block mb-2 font-semibold">{{ $t('jupyterHubConfig.defaultCPU') }}</label>
              <select
                v-model.number="config.default_cpu_cores"
                class="select select-bordered w-full"
              >
                <option v-for="cores in cpuOptions" :key="cores" :value="cores">
                  {{ cores }} core{{ cores > 1 ? 's' : '' }}
                </option>
              </select>
            </div>

            <!-- Memory -->
            <div class="w-full">
              <label class="block mb-2 font-semibold">{{ $t('jupyterHubConfig.defaultMemory') }}</label>
              <select
                v-model.number="config.default_memory_gb"
                class="select select-bordered w-full"
              >
                <option v-for="gb in memoryOptions" :key="gb" :value="gb">
                  {{ gb }} GB
                </option>
              </select>
            </div>

            <!-- GPU -->
            <div class="w-full">
              <label class="block mb-2 font-semibold">{{ $t('jupyterHubConfig.defaultGPU') }}</label>
              <select
                v-model.number="config.default_gpu_count"
                class="select select-bordered w-full"
              >
                <option v-for="i in (selectedNodeCapacity.gpu + 1)" :key="i" :value="i - 1">
                  {{ i - 1 }} GPU{{ (i - 1) !== 1 ? 's' : '' }}
                </option>
              </select>
            </div>
          </div>

          <div v-else class="alert alert-info">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 shrink-0 stroke-current" fill="none" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>{{ $t('jupyterHubConfig.selectNodeFirst') }}</span>
          </div>
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="flex gap-4 justify-end">
        <button @click="loadConfig" class="btn btn-ghost" :disabled="saving">
          {{ $t('jupyterHubConfig.reset') }}
        </button>
        <button @click="saveConfig" class="btn btn-primary" :disabled="saving || !isValid">
          <span v-if="saving" class="loading loading-spinner loading-sm"></span>
          <span>{{ $t('jupyterHubConfig.save') }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import axios from 'axios'

const { t } = useI18n()

const loading = ref(true)
const saving = ref(false)
const error = ref(null)
const saveSuccess = ref(false)

const availableImages = ref([])
const clusterNodes = ref([])

const config = ref({
  hidden_images: [],
  default_image: '',
  default_node: null,
  default_cpu_cores: null,
  default_memory_gb: null,
  default_gpu_count: null
})

const selectedNodeCapacity = computed(() => {
  if (!config.value.default_node) return null
  const node = clusterNodes.value.find(n => n.name === config.value.default_node)
  if (!node) return null

  return {
    cpu: Math.floor(node.capacity.cpu),
    memory: parseInt(node.capacity.memory),
    gpu: node.capacity.gpu
  }
})

const cpuOptions = computed(() => {
  if (!selectedNodeCapacity.value) return []
  const options = [1, 2, 4, 6, 8, 12, 16, 24, 32]
  return options.filter(cores => cores <= selectedNodeCapacity.value.cpu)
})

const memoryOptions = computed(() => {
  if (!selectedNodeCapacity.value) return []
  const options = [2, 4, 8, 16, 32, 48, 64, 96, 128]
  return options.filter(gb => gb <= selectedNodeCapacity.value.memory)
})

const isValid = computed(() => {
  return config.value.default_image &&
         config.value.default_node &&
         !config.value.hidden_images.includes(config.value.default_image)
})

function parseMemoryToGB(memoryStr) {
  if (memoryStr.endsWith('Gi')) {
    return parseInt(memoryStr)
  } else if (memoryStr.endsWith('Mi')) {
    return Math.floor(parseInt(memoryStr) / 1024)
  }
  return 0
}

async function loadConfig() {
  loading.value = true
  error.value = null
  saveSuccess.value = false

  try {
    // Load cluster resources
    const resourcesResponse = await axios.get('/cluster/resources')
    clusterNodes.value = resourcesResponse.data.map(node => ({
      ...node,
      capacity: {
        ...node.capacity,
        memory: parseMemoryToGB(node.capacity.memory)
      }
    }))

    // Load available images
    const imagesResponse = await axios.get('/images/jupyter')
    availableImages.value = imagesResponse.data

    // Load current configuration - no fallbacks
    const configResponse = await axios.get('/jupyterhub/config')
    config.value = {
      hidden_images: configResponse.data.hidden_images,
      default_image: configResponse.data.default_image,
      default_node: configResponse.data.default_node,
      default_cpu_cores: configResponse.data.default_cpu_cores,
      default_memory_gb: configResponse.data.default_memory_gb,
      default_gpu_count: configResponse.data.default_gpu_count
    }
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to load configuration'
    console.error('Error loading JupyterHub config:', err)
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  if (!isValid.value) {
    return
  }

  saving.value = true
  error.value = null
  saveSuccess.value = false

  try {
    await axios.put('/jupyterhub/config', {
      hidden_images: config.value.hidden_images,
      default_image: config.value.default_image,
      default_node: config.value.default_node,
      default_cpu_cores: config.value.default_cpu_cores,
      default_memory_gb: config.value.default_memory_gb,
      default_gpu_count: config.value.default_gpu_count
    })

    saveSuccess.value = true
    setTimeout(() => {
      saveSuccess.value = false
    }, 3000)
  } catch (err) {
    error.value = err.response?.data?.detail || 'Failed to save configuration'
    console.error('Error saving JupyterHub config:', err)
  } finally {
    saving.value = false
  }
}

function toggleImageVisibility(imageName) {
  const index = config.value.hidden_images.indexOf(imageName)
  if (index > -1) {
    config.value.hidden_images.splice(index, 1)
  } else {
    config.value.hidden_images.push(imageName)
    // If hiding the default image, clear it
    if (config.value.default_image === imageName) {
      const visibleImages = availableImages.value.filter(
        img => !config.value.hidden_images.includes(img.name)
      )
      config.value.default_image = visibleImages.length > 0 ? visibleImages[0].name : ''
    }
  }
}

function setDefaultImage(imageName) {
  if (!config.value.hidden_images.includes(imageName)) {
    config.value.default_image = imageName
  }
}

function onNodeChange() {
  // Validate current resources against new node capacity
  if (selectedNodeCapacity.value) {
    if (config.value.default_cpu_cores > selectedNodeCapacity.value.cpu) {
      config.value.default_cpu_cores = selectedNodeCapacity.value.cpu
    }
    if (config.value.default_memory_gb > selectedNodeCapacity.value.memory) {
      config.value.default_memory_gb = selectedNodeCapacity.value.memory
    }
    if (config.value.default_gpu_count > selectedNodeCapacity.value.gpu) {
      config.value.default_gpu_count = selectedNodeCapacity.value.gpu
    }
  }
}

onMounted(() => {
  loadConfig()
})
</script>