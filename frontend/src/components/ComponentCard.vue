<!-- src/components/ComponentCard.vue -->
<template>
  <div class="flex">
    <div class="card h-full bg-base-100 shadow-xl w-full">
      <div class="card-body flex flex-col">
      <!-- Header with icon and title -->
      <div class="flex items-start space-x-3">
        <img :src="component.icon" :alt="component.display_name" class="w-12 h-12" />
        <div class="flex-1">
          <h3 class="card-title">{{ component.display_name }}</h3>
          <div class="flex items-center space-x-2 mt-1">
            <!-- Installation status badge -->
            <span v-if="component.installed" class="badge badge-success gap-1">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="w-3 h-3 stroke-current">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
              </svg>
              Installed
            </span>
            <span v-else class="badge badge-ghost">Not Installed</span>
          </div>
        </div>
      </div>

      <!-- Description -->
      <p class="text-sm text-base-content/70 mt-3">
        {{ component.description }}
      </p>

      <!-- Requirements -->
      <div v-if="component.requirements && component.requirements.length > 0" class="mt-3">
        <div class="text-xs font-semibold text-base-content/60 mb-1">Requirements:</div>
        <div class="flex flex-wrap gap-1">
          <span 
            v-for="req in component.requirements" 
            :key="req"
            class="badge badge-sm"
            :class="isMissingRequirement(req) ? 'badge-error' : 'badge-ghost'"
          >
            {{ req }}
          </span>
        </div>
      </div>

      <!-- Missing requirements alert -->
      <div v-if="!component.requirements_met && component.missing_requirements.length > 0" 
           class="alert alert-warning alert-sm mt-3">
        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 stroke-current shrink-0" fill="none" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
        <span class="text-xs">Missing: {{ component.missing_requirements.join(', ') }}</span>
      </div>

      <!-- Actions -->
      <div class="card-actions justify-end mt-auto">
        <button 
          v-if="!component.installed"
          @click="$emit('install', component)"
          :disabled="!component.requirements_met && !allowForceInstall"
          class="btn btn-primary btn-sm"
          :class="{ 'btn-disabled': !component.requirements_met && !allowForceInstall }"
        >
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
          </svg>
          Install
        </button>
        
        <button 
          v-else
          @click="$emit('uninstall', component)"
          class="btn btn-error btn-sm"
        >
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
            <path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
          </svg>
          Uninstall
        </button>
      </div>
    </div>
  </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  component: {
    type: Object,
    required: true
  },
  allowForceInstall: {
    type: Boolean,
    default: false
  }
})

defineEmits(['install', 'uninstall'])

function isMissingRequirement(req) {
  return props.component.missing_requirements && props.component.missing_requirements.includes(req)
}
</script>