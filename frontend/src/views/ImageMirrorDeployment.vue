<template>
  <div class="container mx-auto px-4 py-8">
    <h1 class="font-bold mb-6">Image Mirror Deployment</h1>

    <div class="card bg-base-100 shadow-xl">
      <div class="card-body">
        <h2 class="card-title">{{ deploymentName }}</h2>

        <div v-if="deployment" class="mb-4">
          <div class="flex items-center gap-4">
            <span class="text-sm">Status:</span>
            <span class="badge" :class="statusBadgeClass">{{ deployment.status }}</span>
          </div>
          <div v-if="deployment.variables" class="mt-2 text-sm">
            <div>Source: {{ deployment.variables.source_image }}</div>
            <div>Description: {{ deployment.variables.image_description || '-' }}</div>
          </div>
        </div>

        <!-- Playbook Executor for WebSocket streaming -->
        <PlaybookExecutor
          ref="playbookExecutor"
          :title="`Mirroring: ${deploymentName}`"
          @complete="onDeploymentComplete"
        />

        <div v-if="deploymentComplete" class="mt-6">
          <div class="alert" :class="deploymentSuccess ? 'alert-success' : 'alert-error'">
            <div>
              <svg v-if="deploymentSuccess" xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <svg v-else xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span>{{ deploymentMessage }}</span>
            </div>
          </div>

          <div class="card-actions justify-end mt-4">
            <button @click="goToImages" class="btn btn-primary">
              Back to Images
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PlaybookExecutor from '@/components/PlaybookExecutor.vue'
import { api } from '@/services/api'

const route = useRoute()
const router = useRouter()

const deploymentId = computed(() => route.params.deploymentId)
const deployment = ref(null)
const deploymentName = ref('Image Mirror')
const playbookExecutor = ref(null)
const deploymentComplete = ref(false)
const deploymentSuccess = ref(false)
const deploymentMessage = ref('')

const statusBadgeClass = computed(() => {
  if (!deployment.value) return 'badge-ghost'
  switch (deployment.value.status) {
    case 'success': return 'badge-success'
    case 'failed': return 'badge-error'
    case 'running': return 'badge-warning'
    case 'pending': return 'badge-info'
    default: return 'badge-ghost'
  }
})

onMounted(async () => {
  if (!deploymentId.value) {
    console.error('No deployment ID provided')
    router.push('/harbor-images')
    return
  }

  try {
    // Get deployment details
    const response = await api.get(`/templates/deployments/${deploymentId.value}`)
    deployment.value = response.data

    if (deployment.value.variables?.source_image) {
      const source = deployment.value.variables.source_image
      const imageName = source.split('/').pop().split(':')[0]
      deploymentName.value = `Mirror: ${imageName}`
    }

    // Start WebSocket connection if deployment is pending or running
    if (deployment.value.status === 'pending' || deployment.value.status === 'running') {
      const wsUrl = `/api/v1/ws/harbor/mirror/${deploymentId.value}`
      playbookExecutor.value?.startExecution(wsUrl)
    } else {
      // Deployment already completed
      deploymentComplete.value = true
      deploymentSuccess.value = deployment.value.status === 'success'
      deploymentMessage.value = deployment.value.status === 'success'
        ? 'Image mirrored successfully!'
        : 'Image mirroring failed'
    }
  } catch (error) {
    console.error('Failed to load deployment:', error)
    alert('Failed to load deployment details')
    router.push('/harbor-images')
  }
})

const onDeploymentComplete = (success) => {
  deploymentComplete.value = true
  deploymentSuccess.value = success
  deploymentMessage.value = success
    ? 'Image mirrored successfully!'
    : 'Image mirroring failed'

  // Reload deployment status
  loadDeploymentStatus()
}

const loadDeploymentStatus = async () => {
  try {
    const response = await api.get(`/templates/deployments/${deploymentId.value}`)
    deployment.value = response.data
  } catch (error) {
    console.error('Failed to reload deployment status:', error)
  }
}

const goToImages = () => {
  router.push('/harbor-images')
}

onUnmounted(() => {
  // Cleanup WebSocket connection if needed
  if (playbookExecutor.value) {
    // PlaybookExecutor should handle its own cleanup
  }
})
</script>