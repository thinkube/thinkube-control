<!-- src/components/PlaybookExecutor.vue -->
<template>
  <div class="playbook-executor">
    <!-- Progress Modal -->
    <dialog class="modal" :open="isExecuting">
      <div class="modal-box max-w-3xl max-h-[90vh]">
        <h3 class="font-bold text-lg mb-4">{{ title }}</h3>
        
        <!-- Task Progress -->
        <div v-if="currentTask" class="mb-4">
          <div class="flex justify-between text-sm mb-1">
            <span class="font-semibold">{{ currentTask }}</span>
            <span v-if="taskCount > 0" class="text-sm text-base-content text-opacity-70">
              Task {{ taskCount }}
            </span>
          </div>
        </div>
        
        <!-- Live Output Log -->
        <div class="mb-4">
          <div class="flex justify-between items-center mb-2">
            <span class="text-sm text-gray-600">Live Output:</span>
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
              v-for="(log, idx) in logOutput"
              :key="idx"
              :data-prefix="getLogPrefix(log.type)"
              :class="getLogClass(log.type)"
            ><code v-html="formatLogMessage(log.message)"></code></pre>
          </div>
        </div>
        
        <!-- Status Summary -->
        <div v-if="taskSummary.totalTasks > 0" class="stats stats-horizontal w-full mb-4">
          <div class="stat place-items-center py-2">
            <div class="stat-title text-xs">Tasks</div>
            <div class="stat-value text-lg">{{ taskSummary.totalTasks }}</div>
          </div>
          <div class="stat place-items-center py-2">
            <div class="stat-title text-xs">OK</div>
            <div class="stat-value text-lg text-success">{{ taskSummary.ok }}</div>
          </div>
          <div class="stat place-items-center py-2">
            <div class="stat-title text-xs">Changed</div>
            <div class="stat-value text-lg text-warning">{{ taskSummary.changed }}</div>
          </div>
          <div v-if="taskSummary.failed > 0" class="stat place-items-center py-2">
            <div class="stat-title text-xs">Failed</div>
            <div class="stat-value text-lg text-error">{{ taskSummary.failed }}</div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="modal-footer">
          <!-- Cancel Button (during execution) -->
          <button v-if="status === 'running'" class="btn btn-outline btn-sm" @click="cancelExecution" :disabled="isCancelling">
            <span v-if="isCancelling" class="loading loading-spinner loading-xs"></span>
            {{ isCancelling ? 'Cancelling...' : 'Cancel' }}
          </button>

          <!-- Close Button (on error) -->
          <button v-if="status === 'error' || status === 'failed'" class="btn btn-sm" @click="closeExecutor">
            Close
          </button>
        </div>
      </div>
    </dialog>

    <!-- Result Modal -->
    <dialog class="modal" :open="showResult">
      <div class="modal-box max-w-2xl">
        <h3 class="font-bold text-lg mb-4">{{ title }} - Complete</h3>
        
        <!-- Success Result -->
        <div v-if="status === 'success'" class="alert alert-success mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>{{ message || successMessage || 'Playbook completed successfully' }}</span>
        </div>
        
        <!-- Error Result -->
        <div v-if="status === 'error'">
          <div class="alert alert-error mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>{{ message || 'Playbook execution failed' }}</span>
          </div>
          
          <!-- GitHub Issue Helper -->
          <div class="alert alert-info mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
            </svg>
            <div>
              <p class="font-semibold">Need help?</p>
              <p class="text-sm">Copy the log output and create an issue on GitHub for assistance.</p>
            </div>
          </div>
        </div>
        
        <!-- Final Summary -->
        <div v-if="taskSummary.totalTasks > 0" class="mb-4">
          <p class="text-sm text-gray-600 mb-2">Execution Summary:</p>
          <div class="text-sm">
            <p>Total Tasks: {{ taskSummary.totalTasks }}</p>
            <p class="text-success">Completed: {{ taskSummary.completedTasks }}</p>
            <p v-if="taskSummary.failedTasks > 0" class="text-error">Failed: {{ taskSummary.failedTasks }}</p>
          </div>
        </div>
        
        <!-- Execution Time -->
        <div v-if="duration" class="mb-4">
          <div class="text-sm text-gray-600">
            Completed in {{ formatDuration(duration) }}
          </div>
        </div>

        <!-- Actions -->
        <div class="modal-footer">
          <button
            class="btn btn-ghost btn-sm gap-1"
            @click="copyOutput"
            :class="{ 'btn-success': copySuccess }"
          >
            <svg v-if="!copySuccess" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"></path>
            </svg>
            <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>
            {{ copySuccess ? 'Copied!' : 'Copy Log' }}
          </button>
          <button class="btn btn-primary btn-sm" @click="closeResult">
            {{ status === 'success' ? 'Continue' : 'Close' }}
          </button>
          <button v-if="status === 'error' && onRetry" class="btn btn-outline btn-sm" @click="retry">
            Retry
          </button>
        </div>
      </div>
      <div class="modal-backdrop" @click="closeResult"></div>
    </dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { incrementWebSocketCount, decrementWebSocketCount } from '@/services/api'

interface PlaybookExecutorProps {
  title: string
  websocketPath?: string  // For direct WebSocket connection
  deploymentId?: string  // For deployment monitoring
  successMessage?: string  // Custom success message
  onRetry?: () => void
  onComplete?: (result: any) => void
}

interface LogEntry {
  type: 'start' | 'play' | 'task' | 'ok' | 'changed' | 'failed' | 'skipped' | 'output' | 'error' | 'complete' | 'info' | 'warning'
  message: string
  task?: string
  task_name?: string
  task_number?: number
}

const props = defineProps<PlaybookExecutorProps>()

const emit = defineEmits(['complete', 'continue'])

// Reactive state
const isExecuting = ref(false)
const showResult = ref(false)
const status = ref<'pending' | 'running' | 'success' | 'error' | 'cancelled'>('pending')
const message = ref('')
const currentTask = ref('')
const taskCount = ref(0)
const duration = ref<number | null>(null)
const isCancelling = ref(false)
const logOutput = ref<LogEntry[]>([])
const logContainer = ref<HTMLElement>()
const autoScroll = ref(true)
const websocket = ref<WebSocket | null>(null)
const startTime = ref<number>(0)
const copySuccess = ref(false)

// Task summary - track unique tasks rather than host executions
const taskSummary = ref({
  totalTasks: 0,
  completedTasks: 0,
  failedTasks: 0,
  ok: 0,
  changed: 0,
  failed: 0
})
const seenTasks = ref(new Set())

// Methods
const startExecution = (wsPath?: string) => {
  isExecuting.value = true
  showResult.value = false
  status.value = 'running'
  message.value = ''
  currentTask.value = 'Connecting...'
  taskCount.value = 0
  duration.value = null
  isCancelling.value = false
  logOutput.value = []
  taskSummary.value = { totalTasks: 0, completedTasks: 0, failedTasks: 0, ok: 0, changed: 0, failed: 0 }
  seenTasks.value = new Set()
  startTime.value = Date.now()
  
  // Connect WebSocket
  const path = wsPath || props.websocketPath
  if (path) {
    connectWebSocket(path)
  } else if (props.deploymentId) {
    connectDeploymentWebSocket(props.deploymentId)
  }
}

const connectWebSocket = (path: string) => {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${wsProtocol}//${window.location.host}${path}`
  console.log('Connecting to WebSocket URL:', wsUrl)
  
  try {
    websocket.value = new WebSocket(wsUrl)
    
    websocket.value.onopen = () => {
      console.log('WebSocket connected')
      incrementWebSocketCount()
      logOutput.value.push({
        type: 'info',
        message: 'Connected to execution service'
      })
    }
    
    websocket.value.onmessage = (event) => {
      const data = JSON.parse(event.data)
      handleWebSocketMessage(data)
    }
    
    websocket.value.onerror = (error) => {
      console.error('WebSocket error:', error)
      logOutput.value.push({
        type: 'error',
        message: 'Connection error occurred'
      })
    }
    
    websocket.value.onclose = () => {
      console.log('WebSocket disconnected')
      decrementWebSocketCount()
      if (status.value === 'running') {
        status.value = 'error'
        message.value = 'Connection lost'
        completeExecution({
          status: 'error',
          message: 'Connection to server lost'
        })
      }
    }
  } catch (error) {
    console.error('Error creating WebSocket:', error)
    logOutput.value.push({
      type: 'error',
      message: `Failed to connect: ${error}`
    })
    completeExecution({
      status: 'error',
      message: 'Failed to establish connection'
    })
  }
}

const connectDeploymentWebSocket = (deploymentId: string) => {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${wsProtocol}//${window.location.host}/api/v1/ws/deployment/${deploymentId}`
  connectWebSocket(wsUrl)
}

const handleWebSocketMessage = (data: any) => {
  // Add to log
  logOutput.value.push({
    type: data.type,
    message: data.message,
    task: data.task,
    task_name: data.task_name,
    task_number: data.task_number
  })
  
  // Auto-scroll to bottom
  if (autoScroll.value) {
    nextTick(() => {
      if (logContainer.value) {
        logContainer.value.scrollTop = logContainer.value.scrollHeight
      }
    })
  }
  
  // Handle different message types
  switch (data.type) {
    case 'start':
      currentTask.value = 'Starting execution...'
      break
      
    case 'task':
      currentTask.value = data.task_name || data.message
      taskCount.value = data.task_number || taskCount.value + 1
      
      // Track unique tasks only
      if (data.task_name && !seenTasks.value.has(data.task_name)) {
        seenTasks.value.add(data.task_name)
        taskSummary.value.totalTasks++
      }
      break
      
    case 'ok':
    case 'changed':
      // Mark task as completed (count unique tasks, not individual host results)
      if (data.task && !seenTasks.value.has(data.task + '_completed')) {
        seenTasks.value.add(data.task + '_completed')
        taskSummary.value.completedTasks++
      }
      if (data.type === 'ok') taskSummary.value.ok++
      if (data.type === 'changed') taskSummary.value.changed++
      break
      
    case 'failed':
      // Mark task as failed
      if (data.task && !seenTasks.value.has(data.task + '_failed')) {
        seenTasks.value.add(data.task + '_failed')
        taskSummary.value.failedTasks++
      }
      taskSummary.value.failed++
      break
      
    case 'complete':
      duration.value = (Date.now() - startTime.value) / 1000
      status.value = data.status
      message.value = data.message
      completeExecution({
        status: data.status,
        message: data.message,
        duration: duration.value
      })
      break
      
    case 'error':
      status.value = 'error'
      message.value = data.message
      break
  }
}

const completeExecution = (result: any) => {
  websocket.value?.close()
  websocket.value = null

  // On error, keep the execution modal open for log review
  if (result.status === 'error' || result.status === 'failed') {
    // Don't close the modal or show result modal on error
    // User can still see logs and must manually close
    status.value = result.status
  } else {
    // On success, show the result modal
    isExecuting.value = false
    showResult.value = true
  }

  // Emit completion event
  emit('complete', result)

  // Call onComplete prop if provided
  if (props.onComplete) {
    props.onComplete(result)
  }

  // No auto-continue - user must manually close the modal
  // This allows them to read the logs, especially on error
}

const cancelExecution = () => {
  isCancelling.value = true
  websocket.value?.close()
  status.value = 'cancelled'
  message.value = 'Execution was cancelled'
  isExecuting.value = false
  showResult.value = true
  isCancelling.value = false
  
  // Emit completion event with cancelled status
  const result = {
    status: 'cancelled',
    message: 'Execution was cancelled by user',
    duration: duration.value
  }
  emit('complete', result)
  
  // Call onComplete prop if provided
  if (props.onComplete) {
    props.onComplete(result)
  }
}

const closeExecutor = () => {
  // Reset all state
  isExecuting.value = false
  showResult.value = false
  status.value = 'pending'
  logOutput.value = []
  taskSummary.value = { totalTasks: 0, completedTasks: 0, failedTasks: 0, ok: 0, changed: 0, failed: 0 }
  currentTask.value = ''
  message.value = ''
  taskCount.value = 0
  duration.value = null
  seenTasks.value.clear()
}

const closeResult = () => {
  showResult.value = false

  // Only emit continue if the playbook was successful
  if (status.value === 'success') {
    emit('continue')
  }

  // Reset state
  status.value = 'pending'
  message.value = ''
  currentTask.value = ''
  taskCount.value = 0
  duration.value = null
  logOutput.value = []
  taskSummary.value = { totalTasks: 0, completedTasks: 0, failedTasks: 0, ok: 0, changed: 0, failed: 0 }
  seenTasks.value = new Set()
}

const retry = () => {
  if (props.onRetry) {
    // Close the result modal without emitting continue
    showResult.value = false
    
    // Reset state
    status.value = 'pending'
    message.value = ''
    currentTask.value = ''
    taskCount.value = 0
    duration.value = null
    logOutput.value = []
    taskSummary.value = { total: 0, ok: 0, changed: 0, failed: 0, skipped: 0 }
    
    // Call the retry handler
    props.onRetry()
  }
}

const copyOutput = async () => {
  try {
    // Build the output text with metadata
    const header = `${props.title} - Execution Log
=====================================
Status: ${status.value}
Duration: ${duration.value ? formatDuration(duration.value) : 'N/A'}
Tasks: Total ${taskSummary.value.totalTasks}, Completed ${taskSummary.value.completedTasks}, Failed ${taskSummary.value.failedTasks}
Timestamp: ${new Date().toISOString()}
=====================================

`
    
    // Combine all log messages
    const logText = logOutput.value
      .map(log => log.message)
      .join('\n')
    
    const fullText = header + logText
    
    // Copy to clipboard
    await navigator.clipboard.writeText(fullText)
    
    // Show success feedback
    copySuccess.value = true
    setTimeout(() => {
      copySuccess.value = false
    }, 2000)
  } catch (err) {
    console.error('Failed to copy output:', err)
    alert('Failed to copy output to clipboard')
  }
}

const formatDuration = (seconds: number): string => {
  if (seconds < 60) {
    return `${seconds.toFixed(1)} seconds`
  } else {
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = Math.floor(seconds % 60)
    return `${minutes}m ${remainingSeconds}s`
  }
}

const getLogClass = (type: string): string => {
  switch (type) {
    case 'task':
      return 'text-info font-semibold'
    case 'play':
      return 'text-primary font-semibold'
    case 'ok':
      return 'text-success'
    case 'changed':
      return 'text-warning'
    case 'failed':
      return 'text-error'
    case 'error':
      return 'text-error font-semibold'
    case 'skipped':
      return 'text-base-content text-opacity-50'
    case 'warning':
      return 'text-warning font-semibold'
    case 'start':
    case 'complete':
      return 'text-accent font-semibold'
    default:
      return 'text-base-content'
  }
}

const formatLogMessage = (message: string): string => {
  // Escape HTML to prevent XSS
  const escapeHtml = (text: string): string => {
    const div = document.createElement('div')
    div.textContent = text
    return div.innerHTML
  }

  // Escape HTML first, then replace literal \n with <br> tags
  const escaped = escapeHtml(message)
  return escaped.replace(/\\n/g, '<br>')
}

const getLogPrefix = (type: string): string => {
  switch (type) {
    case 'task':
      return '>'
    case 'play':
      return '#'
    case 'ok':
      return '✓'
    case 'changed':
      return '~'
    case 'failed':
      return '✗'
    case 'error':
      return '!'
    case 'skipped':
      return '-'
    case 'warning':
      return '⚠'
    case 'start':
      return '$'
    case 'complete':
      return '>'
    default:
      return ' '
  }
}

// Expose methods to parent component
defineExpose({
  startExecution,
  completeExecution,
  cancelExecution
})
</script>

<style scoped>
/* Custom scrollbar for log container */
.overflow-y-auto::-webkit-scrollbar {
  width: 8px;
}

.overflow-y-auto::-webkit-scrollbar-track {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 4px;
}

.overflow-y-auto::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.3);
  border-radius: 4px;
}

.overflow-y-auto::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.5);
}

/* Terminal-like appearance */
.mockup-code {
  background-color: #1a1a1a !important;
  color: #e0e0e0 !important;
}

.mockup-code pre {
  color: inherit !important;
  white-space: pre-wrap !important; /* Allow line wrapping */
  word-break: break-word !important; /* Break long words */
}

.mockup-code code {
  color: inherit !important;
  background: transparent !important;
}

/* Ensure prefix symbols are visible */
.mockup-code pre::before {
  color: #666 !important;
}

/* Adjust semantic colors for dark background */
.mockup-code .text-info {
  color: #60a5fa !important; /* Bright blue */
}

.mockup-code .text-success {
  color: #4ade80 !important; /* Bright green */
}

.mockup-code .text-warning {
  color: #fbbf24 !important; /* Bright yellow */
}

.mockup-code .text-error {
  color: #f87171 !important; /* Bright red */
}

.mockup-code .text-primary {
  color: #38bdf8 !important; /* Bright cyan */
}

.mockup-code .text-accent {
  color: #fb923c !important; /* Bright orange */
}

.mockup-code .text-secondary {
  color: #a1a1aa !important; /* Light gray */
}

.mockup-code .text-base-content {
  color: #e0e0e0 !important; /* Light gray for regular text */
}
</style>