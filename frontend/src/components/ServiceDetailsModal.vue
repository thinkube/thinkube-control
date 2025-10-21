<!-- src/components/ServiceDetailsModal.vue -->
<template>
  <dialog class="modal" open>
    <div class="modal-box max-w-3xl">
      <form method="dialog">
        <button
          class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2"
          @click="$emit('close')"
        >
          <XMarkIcon class="w-5 h-5" />
        </button>
      </form>
      
      <h3 class="font-bold text-lg mb-4">
        {{ service.display_name }}
        <span
          class="badge badge-sm ml-2"
          :class="typeClass"
        >{{ formatServiceType(service.type) }}</span>
      </h3>
      
      <!-- Loading State -->
      <div
        v-if="loading"
        class="flex justify-center py-8"
      >
        <span class="loading loading-spinner loading-lg" />
      </div>
      
      <!-- Service Details -->
      <div v-else>
        <!-- Basic Info -->
        <div class="grid grid-cols-2 gap-4 mb-6">
          <div>
            <h4 class="font-semibold mb-2">
              {{ t('serviceDetails.basicInfo') }}
            </h4>
            <dl class="space-y-2 text-sm">
              <div>
                <dt class="font-medium">
                  {{ t('serviceDetails.namespace') }}
                </dt>
                <dd class="text-gray-600">
                  {{ service.namespace }}
                </dd>
              </div>
              <div>
                <dt class="font-medium">
                  {{ t('serviceDetails.category') }}
                </dt>
                <dd class="text-gray-600">
                  {{ service.category || '-' }}
                </dd>
              </div>
              <div>
                <dt class="font-medium">
                  {{ t('serviceDetails.status') }}
                </dt>
                <dd>
                  <span
                    class="badge badge-sm"
                    :class="service.is_enabled ? 'badge-success' : 'badge-warning'"
                  >
                    {{ service.is_enabled ? t('common.enabled') : t('common.disabled') }}
                  </span>
                </dd>
              </div>
              <div v-if="service.url">
                <dt class="font-medium">
                  {{ t('serviceDetails.url') }}
                </dt>
                <dd>
                  <a
                    :href="service.url"
                    target="_blank"
                    class="link link-primary text-sm"
                  >
                    {{ service.url }}
                  </a>
                </dd>
              </div>
            </dl>
          </div>
          
          <div>
            <h4 class="font-semibold mb-2">
              {{ t('serviceDetails.health') }}
            </h4>
            <div v-if="healthData">
              <div class="stat p-0">
                <div class="stat-title">
                  {{ t('serviceDetails.uptime') }}
                </div>
                <div class="stat-value text-2xl">
                  {{ healthData.uptime_percentage }}%
                </div>
                <div class="stat-desc">
                  <div>{{ healthData.actual_checks }} {{ t('serviceDetails.checksPerformed') }}</div>
                  <div v-if="healthData.monitoring_coverage < 100" class="text-warning">
                    {{ t('serviceDetails.coverage') }}: {{ healthData.monitoring_coverage }}%
                  </div>
                </div>
              </div>
            </div>
            <div
              v-else
              class="text-sm text-gray-600"
            >
              {{ t('serviceDetails.noHealthData') }}
            </div>
          </div>
        </div>
        
        <!-- Endpoints -->
        <div
          v-if="service.endpoints && service.endpoints.length > 0"
          class="mb-6"
        >
          <h4 class="font-semibold mb-2">
            {{ t('serviceDetails.endpoints') }}
          </h4>
          <div class="space-y-2">
            <div 
              v-for="endpoint in service.endpoints" 
              :key="endpoint.id"
              class="border rounded-lg p-3"
              :class="endpoint.is_primary ? 'border-primary' : 'border-base-300'"
            >
              <div class="flex items-center justify-between">
                <div>
                  <span class="font-medium">{{ endpoint.name }}</span>
                  <span
                    v-if="endpoint.is_primary"
                    class="badge badge-sm badge-primary ml-2"
                  >Primary</span>
                  <span class="badge badge-sm badge-ghost ml-2">{{ endpoint.type }}</span>
                </div>
                <div
                  v-if="endpoint.health_status"
                  class="flex items-center gap-2"
                >
                  <span 
                    class="badge badge-sm"
                    :class="{
                      'badge-success': endpoint.health_status === 'healthy',
                      'badge-error': endpoint.health_status === 'unhealthy',
                      'badge-warning': endpoint.health_status === 'unknown'
                    }"
                  >
                    {{ endpoint.health_status }}
                  </span>
                </div>
              </div>
              <div class="text-sm text-gray-600 mt-1">
                <p v-if="endpoint.description">
                  {{ endpoint.description }}
                </p>
                <a 
                  v-if="endpoint.url && !endpoint.is_internal" 
                  :href="endpoint.url" 
                  target="_blank" 
                  class="link link-primary"
                >
                  {{ endpoint.url }}
                </a>
                <span
                  v-else-if="endpoint.is_internal"
                  class="text-gray-500"
                >
                  Internal endpoint
                </span>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Dependencies -->
        <div
          v-if="details && details.dependencies && details.dependencies.length > 0"
          class="mb-6"
        >
          <h4 class="font-semibold mb-2">
            {{ t('serviceDetails.dependencies') }}
          </h4>
          <div class="flex flex-wrap gap-2">
            <div 
              v-for="dep in details.dependencies" 
              :key="dep.name"
              class="badge"
              :class="dep.enabled ? 'badge-success' : 'badge-error'"
            >
              {{ dep.name }}
            </div>
          </div>
        </div>
        
        <!-- Resource Usage -->
        <div
          v-if="details && details.resource_usage"
          class="mb-6"
        >
          <h4 class="font-semibold mb-2">
            {{ t('serviceDetails.resourceUsage') }}
          </h4>
          <div class="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt class="font-medium">
                {{ t('serviceDetails.cpuRequests') }}
              </dt>
              <dd>{{ details.resource_usage.cpu_requests_millicores }}m</dd>
            </div>
            <div>
              <dt class="font-medium">
                {{ t('serviceDetails.memoryRequests') }}
              </dt>
              <dd>{{ details.resource_usage.memory_requests_human }}</dd>
            </div>
          </div>
        </div>
        
        <!-- Pods Info -->
        <div
          v-if="details && details.pods_info && details.pods_info.length > 0"
          class="mb-6"
        >
          <h4 class="font-semibold mb-2">
            {{ t('serviceDetails.pods') }}
          </h4>
          <div class="space-y-2">
            <div
              v-for="pod in details.pods_info"
              :key="pod.name"
              class="border rounded-lg"
            >
              <!-- Pod Header (clickable) -->
              <div
                class="p-3 cursor-pointer hover:bg-base-200 transition-colors"
                @click="togglePod(pod.name)"
              >
                <div class="flex items-center justify-between">
                  <div class="flex items-center gap-3">
                    <svg
                      class="w-4 h-4 transition-transform"
                      :class="expandedPods[pod.name] ? 'rotate-90' : ''"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    <span class="font-medium">{{ pod.name }}</span>
                    <span
                      class="badge badge-sm"
                      :class="pod.ready ? 'badge-success' : 'badge-warning'"
                    >
                      {{ pod.status }}
                    </span>
                  </div>
                  <div class="flex items-center gap-4 text-sm text-gray-600">
                    <span>Node: {{ pod.node }}</span>
                    <span>Restarts: {{ pod.restart_count }}</span>
                    <button
                      class="btn btn-xs btn-ghost"
                      @click.stop="describePod(pod.name)"
                    >
                      Describe
                    </button>
                  </div>
                </div>
              </div>

              <!-- Pod Details (expandable) -->
              <div
                v-if="expandedPods[pod.name]"
                class="border-t px-3 pb-3"
              >
                <div class="mt-3">
                  <h5 class="font-medium mb-2">Containers ({{ pod.containers?.length || 0 }})</h5>
                  <div class="space-y-2">
                    <div
                      v-for="container in pod.containers"
                      :key="container.name"
                      class="border rounded p-2 bg-base-100"
                    >
                      <div class="flex items-center justify-between">
                        <div>
                          <span class="font-medium">{{ container.name }}</span>
                          <span
                            v-if="container.state"
                            class="ml-2 badge badge-xs"
                            :class="{
                              'badge-success': container.state === 'running',
                              'badge-warning': container.state === 'waiting',
                              'badge-error': container.state === 'terminated'
                            }"
                          >
                            {{ container.state }}
                          </span>
                        </div>
                        <button
                          class="btn btn-xs btn-primary"
                          @click="showContainerLogs(pod.name, container.name)"
                        >
                          View Logs
                        </button>
                      </div>
                      <div class="text-xs text-gray-600 mt-1">
                        <div>Image: {{ container.image }}</div>
                        <div v-if="container.resources">
                          <span v-if="container.resources.cpu_request">
                            CPU: {{ container.resources.cpu_request }}
                          </span>
                          <span v-if="container.resources.memory_request" class="ml-3">
                            Memory: {{ container.resources.memory_request }}
                          </span>
                          <span v-if="container.resources.gpu_request !== '0'" class="ml-3">
                            GPU: {{ container.resources.gpu_request }}
                          </span>
                        </div>
                        <div v-if="container.restart_count > 0" class="text-warning">
                          Restarts: {{ container.restart_count }}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Health History Chart -->
        <div
          v-if="healthData && healthData.health_history.length > 0"
          class="mb-6"
        >
          <h4 class="font-semibold mb-2">
            {{ t('serviceDetails.healthHistory') }}
          </h4>
          <div class="h-48 bg-base-200 rounded-lg p-4">
            <HealthHistoryChart :data="healthData.health_history" />
          </div>
        </div>
        
        <!-- Recent Actions -->
        <div v-if="details && details.recent_actions && details.recent_actions.length > 0">
          <h4 class="font-semibold mb-2">
            {{ t('serviceDetails.recentActions') }}
          </h4>
          <div class="space-y-2">
            <div 
              v-for="action in details.recent_actions" 
              :key="action.id"
              class="flex items-center justify-between text-sm p-2 bg-base-200 rounded"
            >
              <div class="flex items-center gap-2">
                <span class="badge badge-sm">{{ action.action }}</span>
                <span>{{ t(`serviceDetails.actions.${action.action}`) }}</span>
              </div>
              <div class="text-gray-600">
                <span>{{ action.performed_by }}</span>
                <span class="ml-2">{{ formatDate(action.performed_at) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="modal-backdrop" @click="$emit('close')"></div>
  </dialog>

  <!-- Pod Describe Modal -->
  <dialog class="modal" :open="showPodDescribeModal">
    <div class="modal-box max-w-4xl max-h-[85vh]">
      <form method="dialog">
        <button
          class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2"
          @click="showPodDescribeModal = false"
        >
          <XMarkIcon class="w-5 h-5" />
        </button>
      </form>

      <h3 class="font-bold text-lg mb-4">
        Pod: {{ currentPod?.name }}
      </h3>

      <!-- Loading State -->
      <div v-if="loadingPodDescription" class="flex justify-center py-8">
        <span class="loading loading-spinner loading-lg" />
      </div>

      <!-- Description Content -->
      <div v-else class="relative">
        <button
          class="btn btn-sm btn-ghost absolute top-2 right-2"
          @click="copyToClipboard()"
          title="Copy to clipboard"
        >
          <ClipboardDocumentIcon class="size-4" />
        </button>
        <pre class="bg-base-200 p-4 rounded-lg overflow-auto text-xs font-mono"
             style="max-height: calc(85vh - 150px)">{{ podDescription }}</pre>
      </div>

      <div class="modal-footer">
        <button class="btn btn-sm" @click="showPodDescribeModal = false">Close</button>
      </div>
    </div>
    <div class="modal-backdrop" @click="showPodDescribeModal = false"></div>
  </dialog>

  <!-- Container Logs Modal -->
  <dialog class="modal" :open="showLogsModal">
    <div class="modal-box max-w-5xl max-h-[90vh]">
      <div class="sticky top-0 bg-base-100 z-10 pb-3 border-b">
        <form method="dialog">
          <button
            class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2"
            @click="showLogsModal = false"
          >
            <XMarkIcon class="w-5 h-5" />
          </button>
        </form>

        <h3 class="font-bold text-lg mb-3">
          Container Logs: {{ currentContainer?.name }}
        </h3>

        <!-- Controls Bar -->
        <div class="flex flex-wrap gap-2">
          <!-- Line selector -->
          <select v-model="logLines" @change="refreshLogs" class="select select-sm select-bordered">
            <option :value="100">Last 100 lines</option>
            <option :value="500">Last 500 lines</option>
            <option :value="1000">Last 1000 lines</option>
            <option :value="5000">Last 5000 lines</option>
          </select>

          <!-- Search -->
          <input
            v-model="logSearch"
            type="text"
            placeholder="Filter logs..."
            class="input input-sm input-bordered flex-1 min-w-[200px]"
          />

          <!-- Actions -->
          <button
            class="btn btn-sm btn-ghost"
            @click="refreshLogs"
            title="Refresh logs"
            :disabled="loadingLogs"
          >
            <ArrowPathIcon class="size-4" :class="{ 'animate-spin': loadingLogs }" />
          </button>
          <button
            class="btn btn-sm btn-ghost"
            @click="downloadLogs"
            title="Download logs"
          >
            <ArrowDownTrayIcon class="size-4" />
          </button>
        </div>
      </div>

      <!-- Loading State -->
      <div v-if="loadingLogs" class="flex justify-center py-8">
        <span class="loading loading-spinner loading-lg" />
      </div>

      <!-- Logs Display -->
      <div
        v-else
        ref="logsContainer"
        class="mt-4 bg-gray-900 text-gray-100 p-4 rounded-lg font-mono text-xs overflow-auto"
        style="max-height: calc(90vh - 200px); line-height: 1.5"
      >
        <pre class="whitespace-pre-wrap break-words">{{ filteredLogs }}</pre>
      </div>

      <div class="modal-footer">
        <span v-if="logSearch" class="text-sm text-base-content opacity-60 mr-auto">
          Filtered results shown
        </span>
        <button class="btn btn-sm" @click="showLogsModal = false">Close</button>
      </div>
    </div>
    <div class="modal-backdrop" @click="showLogsModal = false"></div>
  </dialog>
</template>

<script setup>
import { ref, onMounted, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon, ArrowPathIcon, ArrowDownTrayIcon, ClipboardDocumentIcon } from '@heroicons/vue/24/outline'
import { useServicesStore } from '@/stores/services'
import HealthHistoryChart from './HealthHistoryChart.vue'

const props = defineProps({
  service: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['close'])

const { t } = useI18n()
const servicesStore = useServicesStore()

const loading = ref(true)
const details = ref(null)
const healthData = ref(null)
const expandedPods = ref({})

// Pod Describe Modal
const showPodDescribeModal = ref(false)
const currentPod = ref(null)
const podDescription = ref('')
const loadingPodDescription = ref(false)

// Container Logs Modal
const showLogsModal = ref(false)
const currentContainer = ref(null)
const containerLogs = ref('')
const loadingLogs = ref(false)
const logLines = ref(500)
const logSearch = ref('')
const logsContainer = ref(null)

// Computed
const typeClass = computed(() => {
  switch (props.service.type) {
    case 'core': return 'badge-primary'
    case 'optional': return 'badge-secondary'
    case 'user_app': return 'badge-accent'
    default: return ''
  }
})

// Methods
function formatServiceType(type) {
  const typeLabels = {
    'core': 'Core',
    'optional': 'Optional',
    'user_app': 'User App'
  }
  return typeLabels[type] || type
}

function formatDate(dateString) {
  return new Date(dateString).toLocaleString()
}

function togglePod(podName) {
  expandedPods.value[podName] = !expandedPods.value[podName]
}

async function describePod(podName) {
  loadingPodDescription.value = true
  currentPod.value = { name: podName }
  showPodDescribeModal.value = true

  try {
    const response = await servicesStore.describePod(props.service.id, podName)
    podDescription.value = response.formatted || JSON.stringify(response, null, 2)
  } catch (error) {
    console.error('Failed to describe pod:', error)
    podDescription.value = 'Error: Failed to get pod description'
  } finally {
    loadingPodDescription.value = false
  }
}

async function showContainerLogs(podName, containerName) {
  currentContainer.value = { name: containerName, pod: podName }
  showLogsModal.value = true
  await refreshLogs()
}

async function refreshLogs() {
  loadingLogs.value = true

  try {
    const response = await servicesStore.getContainerLogs(
      props.service.id,
      currentContainer.value.pod,
      currentContainer.value.name,
      logLines.value
    )
    containerLogs.value = response.logs

    // Auto-scroll to bottom after logs are loaded
    await nextTick()
    if (logsContainer.value) {
      logsContainer.value.scrollTop = logsContainer.value.scrollHeight
    }
  } catch (error) {
    console.error('Failed to get container logs:', error)
    containerLogs.value = 'Error: Failed to get container logs'
  } finally {
    loadingLogs.value = false
  }
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text || podDescription.value).then(() => {
    // Could add a toast notification here
    console.log('Copied to clipboard')
  })
}

function downloadLogs() {
  const blob = new Blob([containerLogs.value], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${currentContainer.value.pod}_${currentContainer.value.name}_logs.txt`
  a.click()
  URL.revokeObjectURL(url)
}

const filteredLogs = computed(() => {
  if (!logSearch.value) return containerLogs.value

  const lines = containerLogs.value.split('\n')
  return lines.filter(line =>
    line.toLowerCase().includes(logSearch.value.toLowerCase())
  ).join('\n')
})

function getLogLevelClass(line) {
  const lower = line.toLowerCase()
  if (lower.includes('error') || lower.includes('fatal')) return 'text-red-400'
  if (lower.includes('warn')) return 'text-yellow-400'
  if (lower.includes('debug')) return 'text-gray-500'
  return ''
}

async function loadData() {
  loading.value = true
  try {
    const [detailsRes, healthRes] = await Promise.all([
      servicesStore.fetchServiceDetails(props.service.id),
      servicesStore.fetchHealthHistory(props.service.id)
    ])

    details.value = detailsRes
    healthData.value = healthRes

    // Debug logging to understand data structure
    console.log('Service details response:', detailsRes)
    console.log('Health data response:', healthRes)
    console.log('Health history length:', healthRes?.health_history?.length)
    console.log('First 3 health history items:', healthRes?.health_history?.slice(0, 3))
    console.log('Has pods_info:', !!detailsRes?.pods_info)
    console.log('Has resource_usage:', !!detailsRes?.resource_usage)
    if (detailsRes?.pods_info) {
      console.log('Pods count:', detailsRes.pods_info.length)
    }
  } catch (error) {
    console.error('Failed to load service details:', error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.modal-box {
  max-height: 90vh;
  overflow-y: auto;
}

dl > div {
  display: flex;
  gap: 0.5rem;
}

dt {
  flex-shrink: 0;
  width: 120px;
}
</style>