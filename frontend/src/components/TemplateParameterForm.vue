<!-- Dynamic form component for template parameters -->
<template>
  <div class="template-parameter-form">
    <!-- Standard fields that are always present -->
    <fieldset class="fieldset">
      <label
        class="fieldset-label"
        for="project-name"
      >
        Project Name
      </label>
      <div class="relative">
        <input 
          id="project-name"
          v-model="formData.project_name" 
          type="text" 
          placeholder="my-awesome-app"
          :class="['input', nameValidation.class]"
          pattern="[a-z][a-z0-9-]*"
          required
          @input="handleProjectNameChange"
          @blur="validateProjectName"
        >
        <span
          v-if="checkingName"
          class="absolute right-3 top-3"
        >
          <span class="loading loading-spinner loading-sm" />
        </span>
      </div>
      <div
        v-if="nameValidation.message"
        :class="['fieldset-caption', nameValidation.messageClass]"
      >
        {{ nameValidation.message }}
      </div>
      <div
        v-else
        class="fieldset-caption"
      >
        Lowercase letters, numbers, and hyphens only
      </div>
      
      <!-- Confirmation dialog for overwriting -->
      <div
        v-if="showOverwriteConfirm"
        class="alert alert-warning mt-2"
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
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
          />
        </svg>
        <div>
          <h3 class="font-bold">Application Already Exists</h3>
          <p>An application named "{{ formData.project_name }}" already exists.</p>
          <p>Do you want to replace it? This will delete the existing application.</p>
          <div class="mt-2">
            <button
              class="btn btn-sm btn-warning"
              @click="confirmOverwrite"
            >
              Yes, Replace
            </button>
            <button
              class="btn btn-sm btn-ghost ml-2"
              @click="cancelOverwrite"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </fieldset>
    
    <fieldset class="fieldset">
      <label
        class="fieldset-label"
        for="project-description"
      >
        Project Description
      </label>
      <input 
        id="project-description"
        v-model="formData.project_description" 
        type="text" 
        placeholder="Brief description of your project"
        class="input"
        @input="updateValue('project_description', $event.target.value)"
      >
    </fieldset>
    
    <!-- Group parameters by group -->
    <div
      v-for="group in parameterGroups"
      :key="group.name"
      class="mb-6"
    >
      <div
        v-if="group.name !== 'default'"
        class="divider"
      >
        {{ group.name }}
      </div>
      
      <!-- Render each parameter based on type -->
      <div
        v-for="param in group.parameters"
        :key="param.name"
      >
        <!-- String input -->
        <fieldset
          v-if="param.type === 'str'"
          class="fieldset"
        >
          <label
            class="fieldset-label"
            :for="`param-${param.name}`"
          >
            {{ formatLabel(param) }}
          </label>
          <input 
            :id="`param-${param.name}`"
            :value="formData[param.name] || param.default || ''"
            type="text"
            :placeholder="param.placeholder || ''"
            class="input"
            :pattern="param.pattern || undefined"
            :required="param.required"
            :minlength="param.minLength"
            :maxlength="param.maxLength"
            @input="updateValue(param.name, $event.target.value)"
          >
          <div
            v-if="param.pattern"
            class="fieldset-caption"
          >
            Format: {{ param.pattern }}
          </div>
        </fieldset>
        
        <!-- Integer input -->
        <fieldset
          v-else-if="param.type === 'int'"
          class="fieldset"
        >
          <label
            class="fieldset-label"
            :for="`param-${param.name}`"
          >
            {{ formatLabel(param) }}
          </label>
          <input 
            :id="`param-${param.name}`"
            :value="formData[param.name] || param.default || 0"
            type="number"
            :placeholder="param.placeholder || ''"
            class="input"
            :min="param.min"
            :max="param.max"
            :required="param.required"
            @input="updateValue(param.name, parseInt($event.target.value))"
          >
          <div
            v-if="param.min !== undefined || param.max !== undefined"
            class="fieldset-caption"
          >
            {{ param.min !== undefined ? `Min: ${param.min}` : '' }}
            {{ param.min !== undefined && param.max !== undefined ? ', ' : '' }}
            {{ param.max !== undefined ? `Max: ${param.max}` : '' }}
          </div>
        </fieldset>
        
        <!-- Boolean checkbox -->
        <fieldset
          v-else-if="param.type === 'bool'"
          class="fieldset"
        >
          <label class="label cursor-pointer">
            <span class="label-text">{{ formatLabel(param) }}</span>
            <input 
              :checked="formData[param.name] !== undefined ? formData[param.name] : param.default"
              type="checkbox"
              class="checkbox"
              @change="updateValue(param.name, $event.target.checked)"
            >
          </label>
        </fieldset>
        
        <!-- Choice dropdown -->
        <fieldset
          v-else-if="param.type === 'choice'"
          class="fieldset"
        >
          <label
            class="fieldset-label"
            :for="`param-${param.name}`"
          >
            {{ formatLabel(param) }}
          </label>
          <select 
            :id="`param-${param.name}`"
            :value="formData[param.name] || param.default || ''"
            class="select"
            :required="param.required"
            @change="updateValue(param.name, $event.target.value)"
          >
            <option
              value=""
              disabled
            >
              Select {{ formatLabel(param) }}
            </option>
            <option
              v-for="choice in param.choices"
              :key="choice"
              :value="choice"
            >
              {{ choice }}
            </option>
          </select>
        </fieldset>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { api } from '@/services/api'

const props = defineProps({
  parameters: {
    type: Array,
    default: () => []
  },
  modelValue: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['update:modelValue', 'validation-change'])

// Local form data
const formData = ref({
  project_name: '',
  project_description: '',
  ...props.modelValue
})

// Name validation state
const checkingName = ref(false)
const nameValidation = ref({
  valid: true,
  message: '',
  class: '',
  messageClass: ''
})
const showOverwriteConfirm = ref(false)
const existingServiceInfo = ref(null)
const nameCheckTimeout = ref(null)

// Group parameters by their group field
const parameterGroups = computed(() => {
  const groups = {}
  
  // Add parameters to groups
  props.parameters.forEach(param => {
    const groupName = param.group || 'default'
    if (!groups[groupName]) {
      groups[groupName] = {
        name: groupName,
        parameters: []
      }
    }
    groups[groupName].parameters.push(param)
  })
  
  // Sort parameters within groups by order
  Object.values(groups).forEach(group => {
    group.parameters.sort((a, b) => (a.order || 999) - (b.order || 999))
  })
  
  // Convert to array and sort groups (default first, then alphabetical)
  return Object.values(groups).sort((a, b) => {
    if (a.name === 'default') return -1
    if (b.name === 'default') return 1
    return a.name.localeCompare(b.name)
  })
})

// Update parent when form data changes
const updateValue = (field, value) => {
  formData.value[field] = value
  emit('update:modelValue', { ...formData.value })
  
  // Emit validation state for project_name
  if (field === 'project_name' || field === '_overwrite_confirmed') {
    emit('validation-change', {
      isValid: nameValidation.value.valid || formData.value._overwrite_confirmed,
      fieldName: 'project_name'
    })
  }
}

// Watch for external changes to modelValue
watch(() => props.modelValue, (newValue) => {
  formData.value = {
    project_name: '',
    project_description: '',
    ...newValue
  }
}, { deep: true })

// Handle project name change with debounced validation
const handleProjectNameChange = (event) => {
  const value = event.target.value
  updateValue('project_name', value)
  
  // Clear existing timeout
  if (nameCheckTimeout.value) {
    clearTimeout(nameCheckTimeout.value)
  }
  
  // Reset validation state
  showOverwriteConfirm.value = false
  nameValidation.value = {
    valid: true,
    message: '',
    class: '',
    messageClass: ''
  }
  
  // Validate format first
  if (value && !value.match(/^[a-z][a-z0-9-]*$/)) {
    nameValidation.value = {
      valid: false,
      message: 'Must start with a letter and contain only lowercase letters, numbers, and hyphens',
      class: 'input-error',
      messageClass: 'text-error'
    }
    return
  }
  
  // Debounce the API check
  if (value) {
    nameCheckTimeout.value = setTimeout(() => {
      validateProjectName()
    }, 500)
  }
}

// Validate project name against existing services
const validateProjectName = async () => {
  const name = formData.value.project_name
  if (!name) return
  
  // Check reserved names
  const reservedNames = [
    'keycloak', 'gitlab', 'harbor', 'argocd', 'argo-workflows',
    'prometheus', 'grafana', 'postgres', 'postgresql', 'redis',
    'seaweedfs', 'gitea', 'devpi', 'awx', 'thinkube-control',
    'code-server', 'mlflow', 'zitadel'
  ]
  
  if (reservedNames.includes(name)) {
    nameValidation.value = {
      valid: false,
      message: `"${name}" is a reserved system service name`,
      class: 'input-error',
      messageClass: 'text-error'
    }
    return
  }
  
  checkingName.value = true
  try {
    const { data } = await api.post('/services/check-name', {
      name: name,
      type: 'user'  // User applications
    })
    
    if (data.available) {
      nameValidation.value = {
        valid: true,
        message: '✓ Name is available',
        class: 'input-success',
        messageClass: 'text-success'
      }
    } else {
      nameValidation.value = {
        valid: false,
        message: data.reason || 'Name is not available',
        class: 'input-warning',
        messageClass: 'text-warning'
      }
      
      // If it's a user app, offer to overwrite
      if (data.existing_service && data.existing_service.type === 'user') {
        existingServiceInfo.value = data.existing_service
        showOverwriteConfirm.value = true
      }
    }
  } catch (error) {
    console.error('Name validation error:', error)
    nameValidation.value = {
      valid: true,  // Don't block on API errors
      message: '',
      class: '',
      messageClass: ''
    }
  } finally {
    checkingName.value = false
  }
}

// Handle overwrite confirmation
const confirmOverwrite = () => {
  nameValidation.value = {
    valid: true,
    message: '⚠️ Will replace existing application',
    class: 'input-warning',
    messageClass: 'text-warning'
  }
  showOverwriteConfirm.value = false
  
  // Add a flag to indicate overwrite is confirmed
  updateValue('_overwrite_confirmed', true)
}

// Handle overwrite cancellation
const cancelOverwrite = () => {
  formData.value.project_name = ''
  updateValue('project_name', '')
  updateValue('_overwrite_confirmed', false)
  showOverwriteConfirm.value = false
  nameValidation.value = {
    valid: true,
    message: '',
    class: '',
    messageClass: ''
  }
}

// Format label from parameter name
const formatLabel = (param) => {
  if (param.description) {
    return param.description
  }
  // Convert snake_case to Title Case
  return param.name
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}
</script>