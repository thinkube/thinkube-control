<template>
  <dialog :open="modelValue" class="modal" @close="close">
    <div class="modal-box max-w-3xl">
      <h3 class="font-bold text-lg mb-4">Image Details</h3>

      <div v-if="image">
        <!-- Basic Info -->
        <div class="grid grid-cols-2 gap-4 mb-6">
          <div>
            <label class="label">
              <span class="label-text font-semibold">Name</span>
            </label>
            <p class="text-lg">{{ image.name }}</p>
          </div>
          <div>
            <label class="label">
              <span class="label-text font-semibold">Tag</span>
            </label>
            <p class="text-lg">
              <span class="badge badge-outline">{{ image.tag }}</span>
            </p>
          </div>
        </div>

        <!-- Category and Protection -->
        <div class="grid grid-cols-2 gap-4 mb-6">
          <div>
            <label class="label">
              <span class="label-text font-semibold">Category</span>
            </label>
            <div
              class="badge badge-lg"
              :class="{
                'badge-info': image.category === 'core',
                'badge-success': image.category === 'custom',
                'badge-warning': image.category === 'user'
              }"
            >
              {{ image.category }}
            </div>
          </div>
          <div>
            <label class="label">
              <span class="label-text font-semibold">Protection Status</span>
            </label>
            <div v-if="image.protected" class="flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              <span>Protected from deletion</span>
            </div>
            <div v-else class="text-base-content/70">
              Not protected
            </div>
          </div>
        </div>

        <!-- URLs -->
        <div class="mb-6">
          <label class="label">
            <span class="label-text font-semibold">Registry URL</span>
          </label>
          <div class="mockup-code">
            <pre><code>{{ image.destination_url || `${image.registry}/${image.repository}:${image.tag}` }}</code></pre>
          </div>
        </div>

        <div v-if="image.source_url" class="mb-6">
          <label class="label">
            <span class="label-text font-semibold">Source URL</span>
          </label>
          <div class="mockup-code">
            <pre><code>{{ image.source_url }}</code></pre>
          </div>
        </div>

        <!-- Description -->
        <div v-if="image.description" class="mb-6">
          <label class="label">
            <span class="label-text font-semibold">Description</span>
          </label>
          <p class="text-base">{{ image.description }}</p>
        </div>

        <!-- Timestamps -->
        <div class="grid grid-cols-2 gap-4 mb-6">
          <div>
            <label class="label">
              <span class="label-text font-semibold">Mirror Date</span>
            </label>
            <p>{{ formatDate(image.mirror_date) }}</p>
          </div>
          <div>
            <label class="label">
              <span class="label-text font-semibold">Last Synced</span>
            </label>
            <p>{{ formatDate(image.last_synced) }}</p>
          </div>
        </div>

        <!-- Vulnerabilities -->
        <div v-if="image.vulnerabilities && Object.keys(image.vulnerabilities).length > 0" class="mb-6">
          <label class="label">
            <span class="label-text font-semibold">Vulnerability Summary</span>
          </label>
          <div class="flex gap-2">
            <div v-if="image.vulnerabilities.critical" class="stat bg-error/10 rounded p-2">
              <div class="stat-title text-xs">Critical</div>
              <div class="stat-value text-error text-lg">{{ image.vulnerabilities.critical }}</div>
            </div>
            <div v-if="image.vulnerabilities.high" class="stat bg-warning/10 rounded p-2">
              <div class="stat-title text-xs">High</div>
              <div class="stat-value text-warning text-lg">{{ image.vulnerabilities.high }}</div>
            </div>
            <div v-if="image.vulnerabilities.medium" class="stat bg-info/10 rounded p-2">
              <div class="stat-title text-xs">Medium</div>
              <div class="stat-value text-info text-lg">{{ image.vulnerabilities.medium }}</div>
            </div>
            <div v-if="image.vulnerabilities.low" class="stat bg-base-200 rounded p-2">
              <div class="stat-title text-xs">Low</div>
              <div class="stat-value text-lg">{{ image.vulnerabilities.low }}</div>
            </div>
          </div>
        </div>

        <!-- Metadata -->
        <div v-if="image.metadata && Object.keys(image.metadata).length > 0" class="mb-6">
          <label class="label">
            <span class="label-text font-semibold">Additional Metadata</span>
          </label>
          <div class="mockup-code">
            <pre><code>{{ JSON.stringify(image.metadata, null, 2) }}</code></pre>
          </div>
        </div>

        <!-- Size -->
        <div v-if="image.size_bytes" class="mb-6">
          <label class="label">
            <span class="label-text font-semibold">Image Size</span>
          </label>
          <p>{{ formatSize(image.size_bytes) }}</p>
        </div>
      </div>

      <!-- Actions -->
      <div class="modal-footer">
        <button @click="close" class="btn">Close</button>
      </div>
    </div>
    <div class="modal-backdrop" @click="close"></div>
  </dialog>
</template>

<script setup>
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  image: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:modelValue', 'close'])

const close = () => {
  emit('update:modelValue', false)
  emit('close')
}

const formatDate = (dateString) => {
  if (!dateString) return 'Never'
  const date = new Date(dateString)
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString()
}

const formatSize = (bytes) => {
  if (!bytes) return 'Unknown'
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  if (bytes === 0) return '0 Bytes'
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i]
}
</script>