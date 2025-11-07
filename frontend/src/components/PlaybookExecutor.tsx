'use client'

import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from 'react'
import { Loader2, Copy, Check } from 'lucide-react'
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkSwitch, TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkSuccessAlert, TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import { getAnsibleLogClassName, getAnsibleLogPrefix, AnsibleLogType } from 'thinkube-style/lib/ansible-log-utils'

interface PlaybookExecutorProps {
  title: string
  websocketPath?: string
  deploymentId?: string
  successMessage?: string
  onRetry?: () => void
  onComplete?: (result: ExecutionResult) => void
  onContinue?: () => void
}

interface LogEntry {
  type: AnsibleLogType | 'info' | 'warning'
  message: string
  task?: string
  task_name?: string
  task_number?: number
}

interface TaskSummary {
  totalTasks: number
  completedTasks: number
  failedTasks: number
  ok: number
  changed: number
  failed: number
}

interface ExecutionResult {
  status: 'success' | 'error' | 'failed' | 'cancelled'
  message?: string
  duration?: number
}

type ExecutionStatus = 'pending' | 'running' | 'success' | 'error' | 'cancelled' | 'failed'

export interface PlaybookExecutorHandle {
  startExecution: (wsPath?: string) => void
  completeExecution: (result: ExecutionResult) => void
  cancelExecution: () => void
}

export const PlaybookExecutor = forwardRef<PlaybookExecutorHandle, PlaybookExecutorProps>(function PlaybookExecutor({
  title,
  websocketPath,
  deploymentId,
  successMessage,
  onRetry,
  onComplete,
  onContinue,
}, ref) {
  // State
  const [isExecuting, setIsExecuting] = useState(false)
  const [showResult, setShowResult] = useState(false)
  const [status, setStatus] = useState<ExecutionStatus>('pending')
  const [message, setMessage] = useState('')
  const [currentTask, setCurrentTask] = useState('')
  const [taskCount, setTaskCount] = useState(0)
  const [duration, setDuration] = useState<number | null>(null)
  const [isCancelling, setIsCancelling] = useState(false)
  const [logOutput, setLogOutput] = useState<LogEntry[]>([])
  const [autoScroll, setAutoScroll] = useState(true)
  const [copySuccess, setCopySuccess] = useState(false)
  const [taskSummary, setTaskSummary] = useState<TaskSummary>({
    totalTasks: 0,
    completedTasks: 0,
    failedTasks: 0,
    ok: 0,
    changed: 0,
    failed: 0,
  })

  // Refs
  const logContainerRef = useRef<HTMLDivElement>(null)
  const websocketRef = useRef<WebSocket | null>(null)
  const startTimeRef = useRef<number>(0)
  const seenTasksRef = useRef<Set<string>>(new Set())

  // WebSocket count tracking (for API service)
  const incrementWebSocketCount = () => {
    // This should be imported from an API utility service when it exists
    // For now, this is a placeholder
  }

  const decrementWebSocketCount = () => {
    // This should be imported from an API utility service when it exists
    // For now, this is a placeholder
  }

  // Auto-scroll effect
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [logOutput, autoScroll])

  // WebSocket message handler
  const handleWebSocketMessage = useCallback((data: any) => {
    // Add to log
    const newEntry: LogEntry = {
      type: data.type,
      message: data.message,
      task: data.task,
      task_name: data.task_name,
      task_number: data.task_number,
    }
    setLogOutput(prev => [...prev, newEntry])

    // Handle different message types
    switch (data.type) {
      case 'start':
        setCurrentTask('Starting execution...')
        break

      case 'task':
        setCurrentTask(data.task_name || data.message)
        setTaskCount(data.task_number || (prev => prev + 1))

        // Track unique tasks only
        if (data.task_name && !seenTasksRef.current.has(data.task_name)) {
          seenTasksRef.current.add(data.task_name)
          setTaskSummary(prev => ({
            ...prev,
            totalTasks: prev.totalTasks + 1,
          }))
        }
        break

      case 'ok':
      case 'changed':
        // Mark task as completed
        if (data.task && !seenTasksRef.current.has(data.task + '_completed')) {
          seenTasksRef.current.add(data.task + '_completed')
          setTaskSummary(prev => ({
            ...prev,
            completedTasks: prev.completedTasks + 1,
            [data.type]: prev[data.type as 'ok' | 'changed'] + 1,
          }))
        } else {
          setTaskSummary(prev => ({
            ...prev,
            [data.type]: prev[data.type as 'ok' | 'changed'] + 1,
          }))
        }
        break

      case 'failed':
        // Mark task as failed
        if (data.task && !seenTasksRef.current.has(data.task + '_failed')) {
          seenTasksRef.current.add(data.task + '_failed')
          setTaskSummary(prev => ({
            ...prev,
            failedTasks: prev.failedTasks + 1,
            failed: prev.failed + 1,
          }))
        } else {
          setTaskSummary(prev => ({
            ...prev,
            failed: prev.failed + 1,
          }))
        }
        break

      case 'complete':
        const executionDuration = (Date.now() - startTimeRef.current) / 1000
        setDuration(executionDuration)
        setStatus(data.status)
        setMessage(data.message)
        completeExecution({
          status: data.status,
          message: data.message,
          duration: executionDuration,
        })
        break

      case 'error':
        setStatus('error')
        setMessage(data.message)
        break
    }
  }, [])

  // Connect WebSocket
  const connectWebSocket = useCallback((path: string) => {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${wsProtocol}//${window.location.host}${path}`
    console.log('Connecting to WebSocket URL:', wsUrl)

    try {
      const ws = new WebSocket(wsUrl)
      websocketRef.current = ws

      ws.onopen = () => {
        console.log('WebSocket connected')
        incrementWebSocketCount()
        setLogOutput(prev => [
          ...prev,
          {
            type: 'info',
            message: 'Connected to execution service',
          },
        ])
      }

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data)
        handleWebSocketMessage(data)
      }

      ws.onerror = (error) => {
        console.error('WebSocket error:', error)
        setLogOutput(prev => [
          ...prev,
          {
            type: 'error',
            message: 'Connection error occurred',
          },
        ])
      }

      ws.onclose = () => {
        console.log('WebSocket disconnected')
        decrementWebSocketCount()
        if (status === 'running') {
          setStatus('error')
          setMessage('Connection lost')
          completeExecution({
            status: 'error',
            message: 'Connection to server lost',
          })
        }
      }
    } catch (error) {
      console.error('Error creating WebSocket:', error)
      setLogOutput(prev => [
        ...prev,
        {
          type: 'error',
          message: `Failed to connect: ${error}`,
        },
      ])
      completeExecution({
        status: 'error',
        message: 'Failed to establish connection',
      })
    }
  }, [handleWebSocketMessage, status])

  // Start execution
  const startExecution = useCallback(
    (wsPath?: string) => {
      setIsExecuting(true)
      setShowResult(false)
      setStatus('running')
      setMessage('')
      setCurrentTask('Connecting...')
      setTaskCount(0)
      setDuration(null)
      setIsCancelling(false)
      setLogOutput([])
      setTaskSummary({
        totalTasks: 0,
        completedTasks: 0,
        failedTasks: 0,
        ok: 0,
        changed: 0,
        failed: 0,
      })
      seenTasksRef.current = new Set()
      startTimeRef.current = Date.now()

      // Connect WebSocket
      const path = wsPath || websocketPath
      if (path) {
        connectWebSocket(path)
      } else if (deploymentId) {
        const deploymentPath = `/api/v1/ws/deployment/${deploymentId}`
        connectWebSocket(deploymentPath)
      }
    },
    [websocketPath, deploymentId, connectWebSocket]
  )

  // Complete execution
  const completeExecution = useCallback(
    (result: ExecutionResult) => {
      websocketRef.current?.close()
      websocketRef.current = null

      // On error, keep the execution modal open for log review
      if (result.status === 'error' || result.status === 'failed') {
        setStatus(result.status)
      } else {
        // On success, show the result modal
        setIsExecuting(false)
        setShowResult(true)
      }

      // Emit completion event
      onComplete?.(result)
    },
    [onComplete]
  )

  // Cancel execution
  const cancelExecution = useCallback(() => {
    setIsCancelling(true)
    websocketRef.current?.close()
    setStatus('cancelled')
    setMessage('Execution was cancelled')
    setIsExecuting(false)
    setShowResult(true)
    setIsCancelling(false)

    const result: ExecutionResult = {
      status: 'cancelled',
      message: 'Execution was cancelled by user',
      duration: duration || undefined,
    }
    onComplete?.(result)
  }, [duration, onComplete])

  // Close executor (on error)
  const closeExecutor = useCallback(() => {
    setIsExecuting(false)
    setShowResult(false)
    setStatus('pending')
    setLogOutput([])
    setTaskSummary({
      totalTasks: 0,
      completedTasks: 0,
      failedTasks: 0,
      ok: 0,
      changed: 0,
      failed: 0,
    })
    setCurrentTask('')
    setMessage('')
    setTaskCount(0)
    setDuration(null)
    seenTasksRef.current.clear()
  }, [])

  // Close result modal
  const closeResult = useCallback(() => {
    setShowResult(false)

    // Only emit continue if the playbook was successful
    if (status === 'success') {
      onContinue?.()
    }

    // Reset state
    setStatus('pending')
    setMessage('')
    setCurrentTask('')
    setTaskCount(0)
    setDuration(null)
    setLogOutput([])
    setTaskSummary({
      totalTasks: 0,
      completedTasks: 0,
      failedTasks: 0,
      ok: 0,
      changed: 0,
      failed: 0,
    })
    seenTasksRef.current = new Set()
  }, [status, onContinue])

  // Retry execution
  const retry = useCallback(() => {
    if (onRetry) {
      setShowResult(false)
      setStatus('pending')
      setMessage('')
      setCurrentTask('')
      setTaskCount(0)
      setDuration(null)
      setLogOutput([])
      setTaskSummary({
        totalTasks: 0,
        completedTasks: 0,
        failedTasks: 0,
        ok: 0,
        changed: 0,
        failed: 0,
      })
      onRetry()
    }
  }, [onRetry])

  // Copy output to clipboard
  const copyOutput = useCallback(async () => {
    try {
      const header = `${title} - Execution Log
=====================================
Status: ${status}
Duration: ${duration ? formatDuration(duration) : 'N/A'}
Tasks: Total ${taskSummary.totalTasks}, Completed ${taskSummary.completedTasks}, Failed ${taskSummary.failedTasks}
Timestamp: ${new Date().toISOString()}
=====================================

`
      const logText = logOutput.map((log) => log.message).join('\n')
      const fullText = header + logText

      await navigator.clipboard.writeText(fullText)
      setCopySuccess(true)
      setTimeout(() => setCopySuccess(false), 2000)
    } catch (err) {
      console.error('Failed to copy output:', err)
      alert('Failed to copy output to clipboard')
    }
  }, [title, status, duration, taskSummary, logOutput])

  // Format duration helper
  const formatDuration = (seconds: number): string => {
    if (seconds < 60) {
      return `${seconds.toFixed(1)} seconds`
    } else {
      const minutes = Math.floor(seconds / 60)
      const remainingSeconds = Math.floor(seconds % 60)
      return `${minutes}m ${remainingSeconds}s`
    }
  }

  // Format log message (escape HTML and replace \n)
  const formatLogMessage = (message: string): string => {
    const div = document.createElement('div')
    div.textContent = message
    const escaped = div.innerHTML
    return escaped.replace(/\\n/g, '<br>')
  }

  // Expose methods via ref (for parent components)
  useImperativeHandle(ref, () => ({
    startExecution,
    completeExecution,
    cancelExecution,
  }), [startExecution, completeExecution, cancelExecution])

  return (
    <>
      {/* Progress Modal */}
      <TkDialogRoot open={isExecuting} onOpenChange={(open) => {
        // Prevent closing during running deployment - force use of Cancel button
        if (!open && status === 'running') return
        if (!open) closeExecutor()
      }}>
        <TkDialogContent className="max-w-3xl max-h-[90vh]">
          <TkDialogHeader>
            <TkDialogTitle>{title}</TkDialogTitle>
          </TkDialogHeader>

          <div className="flex flex-col gap-4"> {/* @allowed-inline */}
            {/* Task Progress */}
            {currentTask && (
              <div>
                <div className="flex justify-between text-sm mb-1"> {/* @allowed-inline */}
                  <span className="font-semibold">{currentTask}</span>
                  {taskCount > 0 && (
                    <span className="text-sm text-muted-foreground">Task {taskCount}</span>
                  )}
                </div>
              </div>
            )}

            {/* Live Output Log */}
            <div>
              <div className="flex justify-between items-center mb-2"> {/* @allowed-inline */}
                <span className="text-sm text-muted-foreground">Live Output:</span>
                <div className="flex items-center gap-2"> {/* @allowed-inline */}
                  <TkButton
                    variant="ghost"
                    size="sm"
                    onClick={copyOutput}
                    className={copySuccess ? 'text-success' : ''}
                  >
                    {copySuccess ? (
                      <>
                        <Check className="w-4 h-4 mr-1" />
                        Copied!
                      </>
                    ) : (
                      <>
                        <Copy className="w-4 h-4 mr-1" />
                        Copy
                      </>
                    )}
                  </TkButton>
                  <div className="flex items-center gap-2"> {/* @allowed-inline */}
                    <TkLabel htmlFor="auto-scroll" className="text-xs cursor-pointer">
                      Auto-scroll
                    </TkLabel>
                    <TkSwitch
                      id="auto-scroll"
                      checked={autoScroll}
                      onCheckedChange={setAutoScroll}
                    />
                  </div>
                </div>
              </div>
              <div
                ref={logContainerRef}
                className="h-96 overflow-y-auto overflow-x-hidden p-4 bg-muted/30 rounded-lg font-mono text-sm" /* @allowed-inline */
              >
                {logOutput.length === 0 ? (
                  <div className="text-muted-foreground">
                    <span className="text-muted-foreground">$ </span>
                    Waiting for output...
                  </div>
                ) : (
                  logOutput.map((log, idx) => (
                    <div key={idx} className={`${getAnsibleLogClassName(log.type as AnsibleLogType)} whitespace-pre-wrap break-all`}>
                      <span className="text-muted-foreground">
                        {getAnsibleLogPrefix(log.type as AnsibleLogType)}{' '}
                      </span>
                      <span dangerouslySetInnerHTML={{ __html: formatLogMessage(log.message) }} />
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Status Summary */}
            {taskSummary.totalTasks > 0 && (
              <div className="grid grid-cols-4 gap-4"> {/* @allowed-inline */}
                <TkCard>
                  <TkCardContent className="text-center py-2"> {/* @allowed-inline */}
                    <div className="text-xs text-muted-foreground">Tasks</div>
                    <div className="text-lg font-bold">{taskSummary.totalTasks}</div>
                  </TkCardContent>
                </TkCard>
                <TkCard>
                  <TkCardContent className="text-center py-2"> {/* @allowed-inline */}
                    <div className="text-xs text-muted-foreground">OK</div>
                    <div className="text-lg font-bold text-success">{taskSummary.ok}</div>
                  </TkCardContent>
                </TkCard>
                <TkCard>
                  <TkCardContent className="text-center py-2"> {/* @allowed-inline */}
                    <div className="text-xs text-muted-foreground">Changed</div>
                    <div className="text-lg font-bold text-warning">{taskSummary.changed}</div>
                  </TkCardContent>
                </TkCard>
                {taskSummary.failed > 0 && (
                  <TkCard>
                    <TkCardContent className="text-center py-2"> {/* @allowed-inline */}
                      <div className="text-xs text-muted-foreground">Failed</div>
                      <div className="text-lg font-bold text-destructive">{taskSummary.failed}</div>
                    </TkCardContent>
                  </TkCard>
                )}
              </div>
            )}
          </div>

          {/* Action Buttons */}
          <TkDialogFooter>
            {status === 'running' && (
              <TkButton variant="outline" size="sm" onClick={cancelExecution} disabled={isCancelling}>
                {isCancelling && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                {isCancelling ? 'Cancelling...' : 'Cancel'}
              </TkButton>
            )}
            {(status === 'error' || status === 'failed') && (
              <TkButton size="sm" onClick={closeExecutor}>
                Close
              </TkButton>
            )}
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>

      {/* Result Modal */}
      <TkDialogRoot open={showResult} onOpenChange={(open) => !open && closeResult()}>
        <TkDialogContent className="max-w-2xl">
          <TkDialogHeader>
            <TkDialogTitle>{title} - Complete</TkDialogTitle>
          </TkDialogHeader>

          <div className="flex flex-col gap-4"> {/* @allowed-inline */}
            {/* Success Result */}
            {status === 'success' && (
              <TkSuccessAlert>
                {message || successMessage || 'Playbook completed successfully'}
              </TkSuccessAlert>
            )}

            {/* Error Result */}
            {status === 'error' && (
              <>
                <TkErrorAlert>
                  {message || 'Playbook execution failed'}
                </TkErrorAlert>

                {/* GitHub Issue Helper */}
                <TkInfoAlert title="Need help?">
                  <p className="text-sm">
                    Copy the log output and create an issue on GitHub for assistance.
                  </p>
                </TkInfoAlert>
              </>
            )}

            {/* Final Summary */}
            {taskSummary.totalTasks > 0 && (
              <div>
                <p className="text-sm text-muted-foreground mb-2">Execution Summary:</p>
                <div className="text-sm">
                  <p>Total Tasks: {taskSummary.totalTasks}</p>
                  <p className="text-success">Completed: {taskSummary.completedTasks}</p>
                  {taskSummary.failedTasks > 0 && (
                    <p className="text-destructive">Failed: {taskSummary.failedTasks}</p>
                  )}
                </div>
              </div>
            )}

            {/* Execution Time */}
            {duration && (
              <div className="text-sm text-muted-foreground">
                Completed in {formatDuration(duration)}
              </div>
            )}
          </div>

          {/* Actions */}
          <TkDialogFooter>
            <TkButton
              variant="ghost"
              size="sm"
              onClick={copyOutput}
              className={copySuccess ? 'text-success' : ''}
            >
              {copySuccess ? (
                <>
                  <Check className="w-4 h-4 mr-1" />
                  Copied!
                </>
              ) : (
                <>
                  <Copy className="w-4 h-4 mr-1" />
                  Copy Log
                </>
              )}
            </TkButton>
            <TkButton size="sm" onClick={closeResult}>
              {status === 'success' ? 'Continue' : 'Close'}
            </TkButton>
            {status === 'error' && onRetry && (
              <TkButton variant="outline" size="sm" onClick={retry}>
                Retry
              </TkButton>
            )}
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>
    </>
  )
})
