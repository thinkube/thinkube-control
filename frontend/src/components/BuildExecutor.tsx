/*
 * Copyright 2025 Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useRef, useImperativeHandle, forwardRef } from 'react'
import { TkDialogRoot, TkDialogContent, TkDialogHeader, TkDialogTitle, TkDialogFooter } from 'thinkube-style/components/modals-overlays'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkSwitch } from 'thinkube-style/components/forms-inputs'
import { useCopyToClipboard } from 'thinkube-style/lib/use-copy-to-clipboard'
import { Copy, Check, CheckCircle2, XCircle } from 'lucide-react'
import { TkLabel } from 'thinkube-style/components/forms-inputs'

interface BuildExecutorProps {
  title?: string
  successMessage?: string
  errorMessage?: string
}

interface LogEntry {
  message: string
  class?: string
  status?: 'error' | 'success'
}

export interface BuildExecutorRef {
  startExecution: (wsUrl: string) => void
}

const BuildExecutor = forwardRef<BuildExecutorRef, BuildExecutorProps>(
  ({ title = 'Build Progress', successMessage, errorMessage }, ref) => {
    // State
    const [isExecuting, setIsExecuting] = useState(false)
    const [showResult, setShowResult] = useState(false)
    const [status, setStatus] = useState<'pending' | 'running' | 'success' | 'error' | 'cancelled'>('pending')
    const [message, setMessage] = useState('')
    const [currentTask, setCurrentTask] = useState('')
    const [isCancelling, setIsCancelling] = useState(false)
    const [logOutput, setLogOutput] = useState<LogEntry[]>([])
    const [autoScroll, setAutoScroll] = useState(true)

    const logContainerRef = useRef<HTMLDivElement>(null)
    const websocketRef = useRef<WebSocket | null>(null)

    // Copy to clipboard hook
    const logText = logOutput.map(entry => entry.message).join('\n')
    const { copy, copied } = useCopyToClipboard(logText)

    // Auto-scroll when new logs are added
    useEffect(() => {
      if (autoScroll && logContainerRef.current) {
        logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
      }
    }, [logOutput, autoScroll])

    // Start execution with WebSocket URL - EXACTLY like PlaybookExecutor
    const startExecution = (wsUrl: string) => {
      console.log('BuildExecutor: Starting build with WebSocket URL:', wsUrl)

      // Reset state
      setIsExecuting(true)
      setShowResult(false)
      setStatus('pending')
      setMessage('')
      setCurrentTask('Connecting to build service...')
      setLogOutput([])
      setIsCancelling(false)

      // Create WebSocket connection
      const ws = new WebSocket(wsUrl)
      websocketRef.current = ws

      ws.onopen = () => {
        console.log('BuildExecutor: WebSocket connected')
        setStatus('running')
        setCurrentTask('Building image...')
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)

          if (data.type === 'log') {
            // Build log output with colorization
            let cssClass = 'text-foreground'

            // Detect error patterns
            if (
              data.message.includes('ERROR') ||
              data.message.includes('error:') ||
              data.message.includes('Failed') ||
              data.message.includes('exit code: 1') ||
              data.message.includes('exit status 1') ||
              data.message.includes('AttributeError') ||
              data.message.includes('subprocess-exited-with-error')
            ) {
              cssClass = 'text-destructive font-bold'
            } else if (data.message.includes('WARNING') || data.message.includes('warning:')) {
              cssClass = 'text-warning'
            } else if (data.message.includes('STEP') || data.message.includes('-->')) {
              cssClass = 'text-info font-medium'
            } else if (data.message.includes('Successfully') || data.message.includes('Complete')) {
              cssClass = 'text-success'
            }

            setLogOutput(prev => [
              ...prev,
              {
                message: data.message,
                class: cssClass
              }
            ])
          } else if (data.type === 'status') {
            // Status update
            setCurrentTask(data.message)

            if (data.status === 'completed') {
              setStatus('success')
              setMessage(data.message || 'Build completed successfully')
              setIsExecuting(false)
              setShowResult(true)
            } else if (data.status === 'failed') {
              setStatus('error')
              setMessage(data.message || 'Build failed')
              // Keep the modal open to see the logs
              setIsExecuting(true) // Keep showing the build output
              setShowResult(false) // Don't show result modal
            }
          } else if (data.type === 'error') {
            // Error message
            setLogOutput(prev => [
              ...prev,
              {
                message: `ERROR: ${data.message}`,
                class: 'text-destructive font-bold',
                status: 'error'
              }
            ])
            setStatus('error')
            setMessage(data.message)
            setCurrentTask(`Build failed: ${data.message}`)
          }
        } catch (error) {
          console.error('Error parsing WebSocket message:', error)
        }
      }

      ws.onerror = (error) => {
        console.error('WebSocket error:', error)
        setStatus('error')
        setMessage('Connection error')
        setIsExecuting(false)
        setShowResult(true)
      }

      ws.onclose = () => {
        console.log('WebSocket connection closed')
        if (status === 'running') {
          setStatus('error')
          setMessage('Connection lost')
          setIsExecuting(false)
          setShowResult(true)
        }
      }
    }

    // Cancel execution
    const cancelExecution = () => {
      if (websocketRef.current && websocketRef.current.readyState === WebSocket.OPEN) {
        setIsCancelling(true)
        websocketRef.current.send(JSON.stringify({ type: 'cancel' }))
        setTimeout(() => {
          if (websocketRef.current) {
            websocketRef.current.close()
          }
          setStatus('cancelled')
          setMessage('Build cancelled by user')
          setIsExecuting(false)
          setShowResult(true)
        }, 1000)
      }
    }

    // Handle close
    const handleClose = () => {
      setIsExecuting(false)
      setShowResult(false)
      if (websocketRef.current) {
        websocketRef.current.close()
        websocketRef.current = null
      }
    }

    // Cleanup on unmount
    useEffect(() => {
      return () => {
        if (websocketRef.current) {
          websocketRef.current.close()
        }
      }
    }, [])

    // Expose methods to parent - EXACTLY like PlaybookExecutor
    useImperativeHandle(ref, () => ({
      startExecution
    }))

    return (
      <>
        {/* Progress Modal */}
        <TkDialogRoot open={isExecuting} onOpenChange={handleClose}>
          <TkDialogContent className="max-w-4xl max-h-[90vh]">
            <TkDialogHeader>
              <TkDialogTitle>{title}</TkDialogTitle>
            </TkDialogHeader>

            {/* Build Status */}
            {currentTask && (
              <div className="mb-4">
                <div className="flex justify-between text-sm mb-1">
                  <span className="font-semibold">{currentTask}</span>
                </div>
              </div>
            )}

            {/* Live Output Log */}
            <div className="mb-4">
              <div className="flex justify-between items-center mb-2">
                <span className="text-sm text-muted-foreground">Build Output:</span>
                <div className="flex items-center gap-2">
                  <TkButton
                    variant="ghost"
                    size="sm"
                    onClick={copy}
                    className={copied ? 'text-success' : ''}
                  >
                    {copied ? (
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
                  <div className="flex items-center gap-2">
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
                className="h-96 overflow-y-auto p-4 bg-[#1a1a1a] rounded-lg font-mono text-xs text-[#e0e0e0] whitespace-pre-wrap break-words" /* @allowed-inline */
              >
                {logOutput.length === 0 ? (
                  <div className="text-muted-foreground/50">
                    $ Waiting for output...
                  </div>
                ) : (
                  logOutput.map((entry, index) => (
                    <div key={index} className={entry.class}>
                      {entry.status === 'error' && '✗ '}
                      {entry.message}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Footer Buttons */}
            <TkDialogFooter>
              {status === 'running' && (
                <TkButton
                  variant="destructive"
                  onClick={cancelExecution}
                  disabled={isCancelling}
                >
                  {isCancelling ? 'Cancelling...' : 'Cancel'}
                </TkButton>
              )}
              {status !== 'running' && status !== 'pending' && (
                <TkButton onClick={handleClose}>Close</TkButton>
              )}
            </TkDialogFooter>
          </TkDialogContent>
        </TkDialogRoot>

        {/* Success Result */}
        <TkDialogRoot open={showResult && status === 'success'} onOpenChange={handleClose}>
          <TkDialogContent>
            <TkDialogHeader>
              <TkDialogTitle className="flex items-center gap-2">
                <CheckCircle2 className="w-6 h-6 text-success" />
                Build Complete
              </TkDialogTitle>
            </TkDialogHeader>
            <div className="py-4">
              {successMessage || 'Build completed successfully!'}
            </div>
            <TkDialogFooter>
              <TkButton onClick={handleClose}>Close</TkButton>
            </TkDialogFooter>
          </TkDialogContent>
        </TkDialogRoot>

        {/* Error Result */}
        <TkDialogRoot open={showResult && status === 'error'} onOpenChange={handleClose}>
          <TkDialogContent>
            <TkDialogHeader>
              <TkDialogTitle className="flex items-center gap-2 text-destructive">
                <XCircle className="w-6 h-6" />
                Build Failed
              </TkDialogTitle>
            </TkDialogHeader>
            <div className="py-4">
              {message || errorMessage || 'Build failed. Please check the logs for details.'}
            </div>
            <TkDialogFooter>
              <TkButton onClick={handleClose}>Close</TkButton>
            </TkDialogFooter>
          </TkDialogContent>
        </TkDialogRoot>
      </>
    )
  }
)

BuildExecutor.displayName = 'BuildExecutor'

export default BuildExecutor
