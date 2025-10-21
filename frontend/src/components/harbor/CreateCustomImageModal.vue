<template>
  <dialog :open="modelValue" class="modal">
    <div class="modal-box max-w-2xl">
      <h3 class="font-bold text-lg mb-4">Create Custom Docker Image</h3>

      <form @submit.prevent="createImage">
        <!-- Creation Mode Selection -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Creation Mode</legend>
          <div class="flex gap-4">
            <label class="flex items-center gap-2">
              <input
                type="radio"
                v-model="formData.creation_mode"
                value="from_base"
                class="radio"
                @change="handleModeChange"
              />
              <span>From Base Image</span>
            </label>
            <label class="flex items-center gap-2">
              <input
                type="radio"
                v-model="formData.creation_mode"
                value="extend_existing"
                class="radio"
                @change="handleModeChange"
              />
              <span>Extend Existing Build</span>
            </label>
          </div>
        </fieldset>

        <!-- Image Name -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Image Name</legend>
          <input
            v-model="formData.name"
            type="text"
            placeholder="my-custom-app"
            class="input"
            required
            pattern="[a-z0-9-]+"
            title="Only lowercase letters, numbers and hyphens"
          />
          <div class="label">This will be the image name in Harbor</div>
        </fieldset>

        <!-- Type -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Image Type</legend>
          <select
            v-model="formData.scope"
            class="select"
            required
            :disabled="formData.creation_mode === 'from_base' && formData.base_image !== 'custom'"
          >
            <option value="standard">Standard</option>
            <option value="jupyter">Jupyter Notebook</option>
          </select>
          <div class="label">
            <span v-if="formData.creation_mode === 'from_base' && formData.base_image !== 'custom'">
              Auto-selected based on base image
            </span>
            <span v-else>
              Select Jupyter if this image will be used with JupyterHub
            </span>
          </div>
        </fieldset>

        <!-- Base Image Selection (for from_base mode) -->
        <fieldset v-if="formData.creation_mode === 'from_base'" class="fieldset mb-4">
          <legend class="fieldset-legend">Base Image</legend>
          <select v-model="formData.base_image" @change="loadTemplate" class="select">
            <!-- Dynamic Base Images grouped by type -->
            <optgroup v-if="jupyterBaseImages.length > 0" label="Jupyter Images">
              <option v-for="image in jupyterBaseImages" :key="image.value" :value="image.value">
                {{ image.label }}
              </option>
            </optgroup>
            <optgroup v-if="standardBaseImages.length > 0" label="Standard Images">
              <option v-for="image in standardBaseImages" :key="image.value" :value="image.value">
                {{ image.label }}
              </option>
            </optgroup>
            <option value="custom">Custom Base Image</option>
          </select>
        </fieldset>

        <!-- Parent Image Selection (for extend_existing mode) -->
        <fieldset v-if="formData.creation_mode === 'extend_existing'" class="fieldset mb-4">
          <legend class="fieldset-legend">Parent Image</legend>
          <select v-model="formData.parent_image_id" @change="loadParentDockerfile" class="select" required>
            <option value="">Select a parent image...</option>
            <optgroup v-for="scope in groupedImages" :key="scope.name" :label="scope.label">
              <option
                v-for="image in scope.images"
                :key="image.id"
                :value="image.id"
              >
                {{ image.name }} ({{ image.status }})
              </option>
            </optgroup>
          </select>
          <div class="label">Select an existing build to extend</div>
        </fieldset>

        <!-- Custom Base Image (when custom is selected) -->
        <fieldset v-if="formData.base_image === 'custom' && formData.creation_mode === 'from_base'" class="fieldset mb-4">
          <legend class="fieldset-legend">Custom Base Image</legend>
          <input
            v-model="formData.custom_base_image"
            type="text"
            placeholder="registry.thinkube.com/library/my-base:latest"
            class="input"
            required
          />
        </fieldset>

        <!-- Description -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Description</legend>
          <textarea
            v-model="formData.description"
            class="textarea h-24"
            placeholder="Describe what this image is for..."
          ></textarea>
        </fieldset>

        <!-- Mark as Base Image -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Image Options</legend>
          <label class="flex items-center gap-2">
            <input
              type="checkbox"
              v-model="formData.is_base"
              class="checkbox"
            />
            <span>Mark as base image (can be used as parent for other images)</span>
          </label>
        </fieldset>

        <!-- Dockerfile Content -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Dockerfile Content</legend>
          <textarea
            v-model="formData.dockerfile_content"
            class="textarea h-48 font-mono text-sm"
            placeholder="# Dockerfile will be auto-generated based on your selections"
            required
          ></textarea>
          <div class="label">You can edit this later in code-server</div>
        </fieldset>

        <div class="modal-footer">
          <button type="button" @click="closeModal" class="btn">Cancel</button>
          <button type="submit" class="btn btn-primary" :disabled="loading">
            <span v-if="loading" class="loading loading-spinner loading-sm mr-2"></span>
            Create Image
          </button>
        </div>
      </form>
    </div>
    <div class="modal-backdrop" @click="closeModal"></div>
  </dialog>
</template>

<script setup>
import { ref, watch, onMounted, computed } from 'vue'
import axios from 'axios'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'image-created'])

const loading = ref(false)
const baseImages = ref({})
const existingBuilds = ref([])
const baseImageOptions = ref([])

const formData = ref({
  creation_mode: 'from_base',
  name: '',
  scope: 'standard',
  base_image: 'library/ubuntu:22.04',
  custom_base_image: '',
  parent_image_id: '',
  description: '',
  dockerfile_content: '',
  is_base: false
})

// Computed property for Jupyter base images
const jupyterBaseImages = computed(() => {
  return baseImageOptions.value.filter(img => img.type === 'jupyter')
})

// Computed property for Standard base images
const standardBaseImages = computed(() => {
  return baseImageOptions.value.filter(img => img.type === 'standard')
})

// Group existing builds by type
const groupedImages = computed(() => {
  const groups = {
    standard: { name: 'standard', label: 'Standard Images', images: [] },
    jupyter: { name: 'jupyter', label: 'Jupyter Images', images: [] }
  }

  existingBuilds.value
    .filter(img => img.is_base || img.status === 'success')
    .forEach(img => {
      // Map old scopes to new types
      let type = 'standard'
      if (img.scope === 'jupyter') {
        type = 'jupyter'
      }
      if (groups[type]) {
        groups[type].images.push(img)
      }
    })

  // Only return groups that have images
  return Object.values(groups).filter(g => g.images.length > 0)
})

const resetForm = () => {
  formData.value = {
    creation_mode: 'from_base',
    name: '',
    scope: 'standard',
    base_image: 'library/ubuntu:22.04',
    custom_base_image: '',
    parent_image_id: '',
    description: '',
    dockerfile_content: '',
    is_base: false
  }
}

const closeModal = () => {
  resetForm()
  emit('update:modelValue', false)
}

// Load base image registry from backend
const loadBaseImages = async () => {
  try {
    const response = await axios.get('/custom-images/base-registry')
    const data = response.data

    // Process the images array directly from the backend
    const allImages = []

    // Backend returns { images: [...], types: [...] }
    if (data.images && Array.isArray(data.images)) {
      data.images.forEach(image => {
        allImages.push({
          value: image.registry_url || `library/${image.name}`,
          label: image.display_name || image.name,
          type: image.type || 'standard',
          source: image.source || 'predefined',
          template: image.template || null
        })
      })
    }

    console.log('Loaded base images:', allImages)
    baseImageOptions.value = allImages

    // Store templates and metadata for later use
    const templates = {}
    allImages.forEach(img => {
      templates[img.value] = {
        template: img.template || null,
        type: img.type,
        source: img.source
      }
    })
    baseImages.value = templates

    // Set a sensible default if the current selection isn't in the list
    if (allImages.length > 0 && !allImages.find(img => img.value === formData.value.base_image)) {
      formData.value.base_image = allImages[0].value
    }
  } catch (error) {
    console.error('Failed to load base images:', error)
    // Add more detailed error logging
    if (error.response) {
      console.error('Response error:', error.response.data)
    }
  }
}

// Load existing builds that can be extended
const loadExistingBuilds = async () => {
  try {
    const response = await axios.get('/custom-images')
    existingBuilds.value = response.data.builds || []
  } catch (error) {
    console.error('Failed to load existing builds:', error)
  }
}

// Handle mode change
const handleModeChange = () => {
  formData.value.dockerfile_content = ''
  formData.value.parent_image_id = ''

  if (formData.value.creation_mode === 'from_base') {
    loadTemplate()
  }
}

// Load template for base image
const loadTemplate = async () => {
  const baseImage = formData.value.base_image

  // Skip if custom base image
  if (baseImage === 'custom') {
    formData.value.dockerfile_content = ''
    formData.value.scope = 'standard' // Reset to standard for custom images
    return
  }

  // Check if we have metadata for this base image
  const baseImageInfo = baseImages.value[baseImage]

  if (baseImageInfo) {
    // Use the type (scope) from the loaded metadata
    formData.value.scope = baseImageInfo.type || 'standard'

    // Use template if available
    if (baseImageInfo.template) {
      formData.value.dockerfile_content = baseImageInfo.template
    } else {
      // No template, generate simple FROM
      formData.value.dockerfile_content = `FROM ${baseImage}

# Image: ${formData.value.name || '<image-name>'}
# Description: ${formData.value.description || 'Custom Docker image'}

WORKDIR /app

# Add your customizations here
`
    }
  } else {
    // Fallback for unknown base images - try to detect type
    const baseImageName = baseImage.replace('library/', '')
    if (baseImageName.includes('jupyter')) {
      formData.value.scope = 'jupyter'
    } else {
      formData.value.scope = 'standard'
    }

    // Generate generic template
    formData.value.dockerfile_content = `FROM ${baseImage}

# Image: ${formData.value.name || '<image-name>'}
# Description: ${formData.value.description || 'Custom Docker image'}

# Update system packages if applicable
${baseImageName.includes('alpine') ? 'RUN apk update && apk upgrade' : baseImageName.includes('ubuntu') || baseImageName.includes('debian') ? 'RUN apt-get update && apt-get upgrade -y && apt-get clean && rm -rf /var/lib/apt/lists/*' : '# Base image package updates handled upstream'}

# Add your customizations here
# Install additional packages
# Copy application files
# Set environment variables

# Default command
${baseImageName.includes('node') ? 'CMD ["node", "index.js"]' : baseImageName.includes('python') ? 'CMD ["python", "app.py"]' : 'CMD ["/bin/sh"]'}
`
  }
}

// Load parent dockerfile
const loadParentDockerfile = async () => {
  if (!formData.value.parent_image_id) return

  try {
    const response = await axios.get(`/custom-images/${formData.value.parent_image_id}/dockerfile`)
    let parentDockerfile = response.data.dockerfile

    // Get parent info to use its registry URL and inherit scope
    const parent = existingBuilds.value.find(b => b.id === formData.value.parent_image_id)

    if (parent) {
      // Replace the FROM line to point to the actual built parent image
      if (parent.registry_url) {
        const fromRegex = /^FROM\s+.+$/m
        if (fromRegex.test(parentDockerfile)) {
          parentDockerfile = parentDockerfile.replace(fromRegex, `FROM ${parent.registry_url}`)
        }
      }

      // Inherit scope from parent
      formData.value.scope = parent.scope || 'general'
    }

    // Extend the parent dockerfile
    formData.value.dockerfile_content = `# Extending from: ${response.data.image_name}
${parentDockerfile}

# ===== Extended customizations =====
# Add your additional customizations below

`
  } catch (error) {
    console.error('Failed to load parent dockerfile:', error)
  }
}

const createImage = async () => {
  loading.value = true

  try {
    let base_image = ''

    if (formData.value.creation_mode === 'from_base') {
      base_image = formData.value.base_image === 'custom'
        ? formData.value.custom_base_image
        : formData.value.base_image
    }

    const payload = {
      name: formData.value.name,
      dockerfile_content: formData.value.dockerfile_content,
      scope: formData.value.scope,
      is_base: formData.value.is_base,
      parent_image_id: formData.value.parent_image_id || null,
      build_config: {
        base_image: base_image,
        description: formData.value.description || '',
        creation_mode: formData.value.creation_mode
      }
    }

    await axios.post('/custom-images', payload)

    emit('image-created')
    closeModal()
  } catch (error) {
    alert(`Failed to create image: ${error.response?.data?.detail || error.message}`)
  } finally {
    loading.value = false
  }
}

// Load data when modal opens
watch(() => props.modelValue, async (newVal) => {
  if (newVal) {
    await Promise.all([
      loadBaseImages(),
      loadExistingBuilds()
    ])
    // Set initial template
    if (formData.value.creation_mode === 'from_base') {
      loadTemplate()
    }
  } else {
    resetForm()
  }
})

onMounted(() => {
  // Load initial data if modal is already open
  if (props.modelValue) {
    loadBaseImages()
    loadExistingBuilds()
  }
})
</script>