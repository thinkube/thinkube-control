import { useEffect, useState } from 'react'
import { Loader2, CheckCircle2, XCircle, Hammer } from 'lucide-react'
import { TkErrorAlert } from 'thinkube-style/components/feedback'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkTooltip, TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays'
import api from '@/lib/axios'

interface RedeployRun {
  id: string
  status: string
  current_step: string | null
  steps_done: number
  reason: string | null
}

const inFlight = (status: string) => status === 'pending' || status === 'running'

/**
 * Rebuilds the code-server image and redeploys code-server.
 *
 * The run takes place in thinkube-control, so it continues while the IDE
 * restarts. A run started earlier, for example before this page was reloaded,
 * is picked up from the server when the page opens.
 */
export function CodeServerRedeploy() {
  const [run, setRun] = useState<RedeployRun | null>(null)
  const [logLines, setLogLines] = useState<string[] | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    api.get('/code-server/redeploy')
      .then(({ data }) => {
        if (data.deployment_id && inFlight(data.status)) {
          setRun({ id: data.deployment_id, status: data.status, current_step: null, steps_done: 0, reason: null })
        }
      })
      .catch(err => console.error('Failed to read the latest code-server redeploy:', err))
  }, [])

  const startRedeploy = async () => {
    setConfirming(false)
    setStarting(true)
    try {
      const { data } = await api.post('/code-server/redeploy')
      setRun({ id: data.deployment_id, status: 'running', current_step: null, steps_done: 0, reason: null })
      setLogLines(null)
    } catch (err: any) {
      alert(`Failed to start the code-server redeploy: ${err.response?.data?.detail ?? err.message}`)
    } finally {
      setStarting(false)
    }
  }

  // The run is state on the server: poll its status until it ends.
  useEffect(() => {
    if (!run || !inFlight(run.status)) return
    let stopped = false
    const poll = async () => {
      try {
        const { data } = await api.get(`/templates/deployments/${run.id}`)
        if (stopped) return
        setRun(prev => prev && prev.id === run.id ? {
          ...prev,
          status: data.status,
          current_step: data.current_step ?? prev.current_step,
          steps_done: data.steps_done ?? prev.steps_done,
          reason: data.reason ?? null,
        } : prev)
      } catch (err) {
        console.error('Failed to read the redeploy status:', err)
      }
    }
    poll()
    const timer = setInterval(poll, 3000)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [run?.id, run?.status])

  const showLog = async () => {
    if (!run) return
    try {
      const { data } = await api.get(`/templates/deployments/${run.id}/logs`, { params: { limit: 1000 } })
      setLogLines(data.logs.map((l: any) => l.message))
    } catch (err: any) {
      setLogLines([`Could not read the log: ${err.message}`])
    }
  }

  return (
    <div className="space-y-4">
      <TkTooltip content="Rebuild the code-server image and redeploy code-server. The workspace repositories are not touched.">
        <TkButton
          intent="secondary"
          onClick={() => setConfirming(true)}
          disabled={starting || (run !== null && inFlight(run.status))}
        >
          <Hammer className="h-4 w-4 mr-2" />
          Redeploy
        </TkButton>
      </TkTooltip>

      {run && (
        <TkCard>
          <TkCardContent className="pt-6 space-y-3">
            <div className="flex items-center gap-2">
              {inFlight(run.status) && <Loader2 className="h-5 w-5 animate-spin text-primary" />}
              {run.status === 'success' && <CheckCircle2 className="h-5 w-5 text-green-600" />}
              {!inFlight(run.status) && run.status !== 'success' && <XCircle className="h-5 w-5 text-destructive" />}
              <h3 className="text-lg font-semibold">Redeploying code-server</h3>
            </div>
            {inFlight(run.status) && (
              <p className="text-sm text-muted-foreground">
                {run.current_step ? `Step ${run.steps_done}: ${run.current_step}` : 'Starting...'}
              </p>
            )}
            {run.status === 'success' && (
              <p className="text-sm">code-server: redeployed, {run.steps_done} steps. Reload the IDE window.</p>
            )}
            {!inFlight(run.status) && run.status !== 'success' && (
              <TkErrorAlert>
                {run.status}{run.reason ? `: ${run.reason}` : ''}. The redeploy can be started again.
              </TkErrorAlert>
            )}
            {logLines && (
              <pre className="text-xs bg-muted rounded p-3 max-h-80 overflow-auto whitespace-pre-wrap">
                {logLines.join('\n')}
              </pre>
            )}
            <div className="flex gap-2 justify-end">
              <TkButton intent="secondary" size="sm" onClick={showLog}>
                {logLines ? 'Refresh log' : 'View log'}
              </TkButton>
              {!inFlight(run.status) && (
                <TkButton intent="secondary" size="sm" onClick={() => { setRun(null); setLogLines(null) }}>
                  Close
                </TkButton>
              )}
            </div>
          </TkCardContent>
        </TkCard>
      )}

      <TkControlledConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Redeploy code-server"
        description="This rebuilds the code-server image and redeploys code-server. The IDE restarts and open sessions in it are interrupted. The workspace repositories are not touched."
        confirmText="Redeploy"
        cancelText="Cancel"
        onConfirm={startRedeploy}
        variant="destructive"
      />
    </div>
  )
}
