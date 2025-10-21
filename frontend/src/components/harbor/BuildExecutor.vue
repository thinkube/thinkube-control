<!-- src/components/harbor/BuildExecutor.vue -->
<template>
  <div class="build-executor">
    <!-- Progress Modal -->
    <dialog :open="isExecuting" class="modal">
      <div class="modal-box max-w-4xl max-h-[90vh]">
        <h3 class="font-bold text-lg mb-4">{{ title }}</h3>

        <!-- Build Status -->
        <div v-if="currentTask" class="mb-4">
          <div class="flex justify-between text-sm mb-1">
            <span class="font-semibold">{{ currentTask }}</span>
          </div>
        </div>

        <!-- Live Output Log -->
        <div class="mb-4">
          <div class="flex justify-between items-center mb-2">
            <span class="text-sm text-gray-600">Build Output:</span>
            <div class="flex items-center gap-2">
              <button
                class="btn btn-ghost btn-xs gap-1"
                @click="copyOutput"
                :class="{ 'btn-success': copySuccess }"
              >
                <svg v-if="!copySuccess" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"></path>
                </svg>
                <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                </svg>
                {{ copySuccess ? 'Copied!' : 'Copy' }}
              </button>
              <label class="label cursor-pointer gap-2">
                <span class="label-text text-xs">Auto-scroll</span>
                <input type="checkbox" v-model="autoScroll" class="checkbox checkbox-xs" />
              </label>
            </div>
          </div>
          <div
            class="mockup-code h-96 overflow-y-auto text-xs"
            ref="logContainer"
          >
            <div v-if="logOutput.length === 0" class="text-base-content text-opacity-50">
              <pre data-prefix="$"><code>Waiting for output...</code></pre>
            </div>
            <pre
              v-for="(entry, index) in logOutput"
              :key="index"
              :class="entry.class"
              :data-prefix="entry.status === 'error' ? '✗' : ''"
            ><code>{{ entry.message }}</code></pre>
          </div>
        </div>

        <!-- Footer Buttons -->
        <div class="modal-footer">
          <button
            v-if="status === 'running'"
            @click="cancelExecution"
            class="btn btn-error"
            :disabled="isCancelling"
          >
            {{ isCancelling ? 'Cancelling...' : 'Cancel' }}
          </button>
          <button
            v-if="status !== 'running' && status !== 'pending'"
            @click="handleClose"
            class="btn"
          >
            Close
          </button>
        </div>
      </div>
      <div class="modal-backdrop"></div>
    </dialog>

    <!-- Success Result -->
    <dialog :open="showResult && status === 'success'" class="modal">
      <div class="modal-box">
        <h3 class="text-lg font-bold flex items-center gap-2">
          <svg class="w-6 h-6 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
          </svg>
          Build Complete
        </h3>
        <p class="py-4">{{ successMessage || 'Build completed successfully!' }}</p>
        <div class="modal-footer">
          <button @click="handleClose" class="btn">Close</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="handleClose"></div>
    </dialog>

    <!-- Error Result -->
    <dialog :open="showResult && status === 'error'" class="modal">
      <div class="modal-box">
        <h3 class="text-lg font-bold flex items-center gap-2 text-error">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
          </svg>
          Build Failed
        </h3>
        <p class="py-4">{{ message || errorMessage || 'Build failed. Please check the logs for details.' }}</p>
        <div class="modal-footer">
          <button @click="handleClose" class="btn">Close</button>
        </div>
      </div>
      <div class="modal-backdrop" @click="handleClose"></div>
    </dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onUnmounted, nextTick } from 'vue'

interface BuildExecutorProps {
  title?: string
  successMessage?: string
  errorMessage?: string
  showCloseButton?: boolean
}

interface LogEntry {
  message: string
  class?: string
  status?: 'error' | 'success'
}

const props = defineProps<BuildExecutorProps>()
const emit = defineEmits(['complete'])

// Reactive state
const isExecuting = ref(false)
const showResult = ref(false)
const status = ref<'pending' | 'running' | 'success' | 'error' | 'cancelled'>('pending')
const message = ref('')
const currentTask = ref('')
const isCancelling = ref(false)
const logOutput = ref<LogEntry[]>([])
const logContainer = ref<HTMLElement>()
const autoScroll = ref(true)
const websocket = ref<WebSocket | null>(null)
const copySuccess = ref(false)

// Copy output to clipboard
const copyOutput = async () => {
  const text = logOutput.value.map(entry => entry.message).join('\n')
  try {
    await navigator.clipboard.writeText(text)
    copySuccess.value = true
    setTimeout(() => {
      copySuccess.value = false
    }, 2000)
  } catch (err) {
    console.error('Failed to copy:', err)
  }
}

// Auto-scroll to bottom when new logs are added
watch(logOutput, async () => {
  if (autoScroll.value && logContainer.value) {
    await nextTick()
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}, { deep: true })

// Start execution with WebSocket URL - EXACTLY like PlaybookExecutor
const startExecution = (wsUrl: string) => {
  console.log('BuildExecutor: Starting build with WebSocket URL:', wsUrl)

  // Reset state
  isExecuting.value = true
  showResult.value = false
  status.value = 'pending'
  message.value = ''
  currentTask.value = 'Connecting to build service...'
  logOutput.value = []
  isCancelling.value = false

  // Create WebSocket connection
  websocket.value = new WebSocket(wsUrl)

  websocket.value.onopen = () => {
    console.log('BuildExecutor: WebSocket connected')
    status.value = 'running'
    currentTask.value = 'Building image...'
  }

  websocket.value.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)

      if (data.type === 'log') {
        // Build log output with colorization
        let cssClass = 'terminal-output'

        // Detect error patterns
        if (data.message.includes('ERROR') || data.message.includes('error:') ||
            data.message.includes('Failed') || data.message.includes('exit code: 1') ||
            data.message.includes('exit status 1') || data.message.includes('AttributeError') ||
            data.message.includes('subprocess-exited-with-error')) {
          cssClass = 'terminal-error'
        } else if (data.message.includes('WARNING') || data.message.includes('warning:')) {
          cssClass = 'terminal-warning'
        } else if (data.message.includes('STEP') || data.message.includes('-->')) {
          cssClass = 'terminal-step'
        } else if (data.message.includes('Successfully') || data.message.includes('Complete')) {
          cssClass = 'terminal-success'
        }

        logOutput.value.push({
          message: data.message,
          class: cssClass
        })
      } else if (data.type === 'status') {
        // Status update
        currentTask.value = data.message

        if (data.status === 'completed') {
          status.value = 'success'
          message.value = data.message || 'Build completed successfully'
          isExecuting.value = false
          showResult.value = true
          emit('complete', { status: 'success', message: message.value })
        } else if (data.status === 'failed') {
          status.value = 'error'
          message.value = data.message || 'Build failed'
          // Keep the modal open to see the logs
          isExecuting.value = true  // Keep showing the build output
          showResult.value = false  // Don't show result modal
          emit('complete', { status: 'error', message: message.value })
        }
      } else if (data.type === 'error') {
        // Error message
        logOutput.value.push({
          message: `ERROR: ${data.message}`,
          class: 'terminal-error',
          status: 'error'
        })
        status.value = 'error'
        message.value = data.message
        currentTask.value = `Build failed: ${data.message}`
      }
    } catch (error) {
      console.error('Error parsing WebSocket message:', error)
    }
  }

  websocket.value.onerror = (error) => {
    console.error('WebSocket error:', error)
    status.value = 'error'
    message.value = 'Connection error'
    isExecuting.value = false
    showResult.value = true
  }

  websocket.value.onclose = () => {
    console.log('WebSocket connection closed')
    if (status.value === 'running') {
      status.value = 'error'
      message.value = 'Connection lost'
      isExecuting.value = false
      showResult.value = true
    }
  }
}

// Cancel execution
const cancelExecution = () => {
  if (websocket.value && websocket.value.readyState === WebSocket.OPEN) {
    isCancelling.value = true
    websocket.value.send(JSON.stringify({ type: 'cancel' }))
    setTimeout(() => {
      if (websocket.value) {
        websocket.value.close()
      }
      status.value = 'cancelled'
      message.value = 'Build cancelled by user'
      isExecuting.value = false
      showResult.value = true
      emit('complete', { status: 'cancelled', message: message.value })
    }, 1000)
  }
}

// Handle close
const handleClose = () => {
  isExecuting.value = false
  showResult.value = false
  if (websocket.value) {
    websocket.value.close()
    websocket.value = null
  }
}

// Cleanup on unmount
onUnmounted(() => {
  if (websocket.value) {
    websocket.value.close()
  }
})

// Expose methods to parent - EXACTLY like PlaybookExecutor
defineExpose({
  startExecution
})
</script>

<style scoped>
/* Terminal output styling optimized for dark backgrounds */
.mockup-code {
  background-color: #1a1a1a !important;
  color: #e0e0e0 !important;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

.mockup-code pre {
  white-space: pre-wrap !important; /* Allow line wrapping */
  word-break: break-word !important; /* Break long words */
}

/* Terminal output colors */
.terminal-output {
  color: #e0e0e0 !important;
}

.terminal-error {
  color: #ef4444 !important;
  font-weight: bold;
}

.terminal-warning {
  color: #fbbf24 !important;
}

.terminal-step {
  color: #60a5fa !important;
  font-weight: 500;
}

.terminal-success {
  color: #34d399 !important;
}
</style>