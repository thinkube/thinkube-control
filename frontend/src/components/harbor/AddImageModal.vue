<template>
  <dialog :open="modelValue" class="modal">
    <div class="modal-box max-w-2xl">
      <h3 class="font-bold text-lg mb-4">Add Image to Mirror</h3>

      <form @submit.prevent="submitForm">
        <!-- Source URL -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Source Image URL <span class="text-error">*</span></legend>
          <input
            v-model="form.source_url"
            type="text"
            placeholder="e.g., docker.io/library/nginx:latest"
            class="input"
            :class="{ 'input-error': errors.source_url }"
            required
          />
          <div v-if="errors.source_url" class="label text-error text-sm">
            {{ errors.source_url }}
          </div>
          <div class="label">
            Examples: docker.io/library/alpine:latest, quay.io/prometheus/prometheus:latest
          </div>
        </fieldset>

        <!-- Description -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Description</legend>
          <textarea
            v-model="form.description"
            class="textarea h-24"
            placeholder="Brief description of what this image is used for"
          ></textarea>
        </fieldset>

        <!-- Auto Mirror -->
        <fieldset class="fieldset mb-4">
          <legend class="fieldset-legend">Mirror Options</legend>
          <label class="flex items-center gap-2">
            <input
              v-model="form.auto_mirror"
              type="checkbox"
              class="checkbox"
            />
            <span>Start mirroring immediately</span>
          </label>
          <div class="label">
            If checked, the image will be mirrored to Harbor right away. Otherwise, it will only be added to the inventory.
          </div>
        </fieldset>

        <!-- Actions -->
        <div class="modal-footer">
          <button
            type="button"
            @click="close"
            class="btn"
            :disabled="loading"
          >
            Cancel
          </button>
          <button
            type="submit"
            class="btn btn-primary"
            :disabled="loading || !isValid"
          >
            <span v-if="loading" class="loading loading-spinner loading-sm mr-2"></span>
            {{ form.auto_mirror ? 'Add & Mirror' : 'Add to Inventory' }}
          </button>
        </div>
      </form>
    </div>
    <div class="modal-backdrop" @click="close"></div>
  </dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useHarborImagesStore } from '@/stores/harborImages'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'image-added'])

const store = useHarborImagesStore()

const loading = ref(false)
const form = ref({
  source_url: '',
  description: '',
  auto_mirror: true
})

const errors = ref({
  source_url: ''
})

const isValid = computed(() => {
  return form.value.source_url && !errors.value.source_url
})

watch(() => form.value.source_url, (value) => {
  if (!value) {
    errors.value.source_url = ''
    return
  }

  const urlPattern = /^[a-zA-Z0-9.-]+\/[a-zA-Z0-9.\/_-]+:[a-zA-Z0-9._-]+$/
  const simplePattern = /^[a-zA-Z0-9.\/_-]+:[a-zA-Z0-9._-]+$/

  if (!urlPattern.test(value) && !simplePattern.test(value)) {
    errors.value.source_url = 'Invalid image URL format. Expected: registry/path:tag'
  } else {
    errors.value.source_url = ''
  }
})

const close = () => {
  emit('update:modelValue', false)
  resetForm()
}

const resetForm = () => {
  form.value = {
    source_url: '',
    description: '',
    auto_mirror: true
  }
  errors.value = {
    source_url: ''
  }
}

const submitForm = async () => {
  if (!isValid.value) return

  loading.value = true

  try {
    const result = await store.addImage({
      source_url: form.value.source_url,
      description: form.value.description,
      auto_mirror: form.value.auto_mirror
    })

    if (result.deployment_id) {
      close()
      window.location.href = `/harbor-images/mirror/${result.deployment_id}`
    } else if (result.job) {
      alert(`Image added successfully! Mirror job started with ID: ${result.job.id}`)
    } else {
      alert('Image added to inventory successfully!')
    }

    emit('image-added', result)
    close()
  } catch (error) {
    console.error('Failed to add image:', error)
    const errorMessage = error.response?.data?.detail || error.message || 'Failed to add image'
    alert(`Error: ${errorMessage}`)
  } finally {
    loading.value = false
  }
}
</script>