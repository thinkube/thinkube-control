/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
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
  onFinished?: () => void
}

interface LogEntry {
  message: string
  class?: string
}

// One reading of a build that runs on the server: its record's status and its log so far.
export interface BuildSnapshot {
  status: string
  log: string
  message?: string
}

export interface BuildExecutorRef {
  follow: (poll: () => Promise<BuildSnapshot>) => void
}

const POLL_INTERVAL_MS = 3000

function logClass(line: string): string {
  if (
    line.includes('ERROR') ||
    line.includes('error:') ||
    line.includes('Failed') ||
    line.includes('FAILED') ||
    line.includes('exit code: 1') ||
    line.includes('exit status 1') ||
    line.includes('AttributeError') ||
    line.includes('subprocess-exited-with-error')
  ) {
    return 'text-destructive font-bold'
  }
  if (line.includes('WARNING') || line.includes('warning:')) return 'text-warning'
  if (line.includes('STEP') || line.includes('-->')) return 'text-info font-medium'
  if (line.includes('Successfully') || line.includes('COMPLETED')) return 'text-success'
  return 'text-foreground'
}

const BuildExecutor = forwardRef<BuildExecutorRef, BuildExecutorProps>(
  ({ title = 'Build Progress', successMessage, errorMessage, onFinished }, ref) => {
    const [isExecuting, setIsExecuting] = useState(false)
    const [showResult, setShowResult] = useState(false)
    const [status, setStatus] = useState<'running' | 'success' | 'error'>('running')
    const [message, setMessage] = useState('')
    const [logOutput, setLogOutput] = useState<LogEntry[]>([])
    const [autoScroll, setAutoScroll] = useState(true)

    const logContainerRef = useRef<HTMLDivElement>(null)
    const timerRef = useRef<number | null>(null)

    const logText = logOutput.map(entry => entry.message).join('\n')
    const { copy, copied } = useCopyToClipboard(logText)

    useEffect(() => {
      if (autoScroll && logContainerRef.current) {
        logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
      }
    }, [logOutput, autoScroll])

    const stopPolling = () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }

    // The build runs on the server; this view reads its record and log until it ends.
    const follow = (poll: () => Promise<BuildSnapshot>) => {
      stopPolling()
      setIsExecuting(true)
      setShowResult(false)
      setStatus('running')
      setMessage('')
      setLogOutput([])

      const tick = async () => {
        let snapshot: BuildSnapshot
        try {
          snapshot = await poll()
        } catch (error: any) {
          setStatus('error')
          setMessage(`Could not read the build: ${error.response?.data?.detail || error.message}`)
          return
        }
        setLogOutput(
          snapshot.log
            .split('\n')
            .filter(line => line.length > 0)
            .map(line => ({ message: line, class: logClass(line) }))
        )
        if (snapshot.status === 'success') {
          setStatus('success')
          setMessage(snapshot.message || '')
          setIsExecuting(false)
          setShowResult(true)
          onFinished?.()
          return
        }
        if (snapshot.status === 'failed') {
          // The progress view stays open so the log can be read.
          setStatus('error')
          setMessage(snapshot.message || errorMessage || 'Build failed. The log above says why.')
          onFinished?.()
          return
        }
        timerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS)
      }
      tick()
    }

    const handleClose = () => {
      stopPolling()
      setIsExecuting(false)
      setShowResult(false)
    }

    useEffect(() => stopPolling, [])

    useImperativeHandle(ref, () => ({
      follow
    }))

    return (
      <>
        {/* Progress Modal */}
        <TkDialogRoot open={isExecuting} onOpenChange={handleClose}>
          <TkDialogContent className="max-w-4xl max-h-[90vh]">
            <TkDialogHeader>
              <TkDialogTitle>{title}</TkDialogTitle>
            </TkDialogHeader>

            <div className="mb-4">
              <div className="flex justify-between text-sm mb-1">
                <span className="font-semibold">
                  {status === 'running' && 'Building on the server. Closing this view does not stop the build.'}
                  {status === 'error' && message}
                </span>
              </div>
            </div>

            {/* Build log */}
            <div className="mb-4">
              <div className="flex justify-between items-center mb-2">
                <span className="text-sm text-muted-foreground">Build Output:</span>
                <div className="flex items-center gap-2">
                  <TkButton
                    intent="ghost"
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
                      {entry.message}
                    </div>
                  ))
                )}
              </div>
            </div>

            <TkDialogFooter>
              <TkButton onClick={handleClose}>Close</TkButton>
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
              {message || successMessage || 'Build completed successfully!'}
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
