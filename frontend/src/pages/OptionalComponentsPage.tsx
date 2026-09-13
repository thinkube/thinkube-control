import { useState, useEffect, useMemo } from 'react'
import { Loader2, CheckCircle2, XCircle } from 'lucide-react'
import { TkErrorAlert } from 'thinkube-style/components/feedback'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { ComponentCard } from '../components/ComponentCard'
import { useComponentsStore } from '../stores/useComponentsStore'
import api from '@/lib/axios'

interface OptionalComponent {
  name: string
  display_name: string
  category: 'ai' | 'data' | 'monitoring' | 'infrastructure'
  description: string
  icon?: string
  is_installed: boolean
  activity?: 'installing' | 'uninstalling' | null
  requirements: string[]
  [key: string]: any
}

interface ComponentRun {
  id: string
  title: string
  component: OptionalComponent
  status: string
  current_step: string | null
  steps_done: number
  reason: string | null
}

export default function OptionalComponentsPage() {
  const store = useComponentsStore()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [components, setComponents] = useState<OptionalComponent[]>([])
  const [run, setRun] = useState<ComponentRun | null>(null)
  const [logLines, setLogLines] = useState<string[] | null>(null)

  // Filter components by category
  const aiComponents = useMemo(() =>
    components.filter(c => c.category === 'ai'),
    [components]
  )

  const dataComponents = useMemo(() =>
    components.filter(c => c.category === 'data'),
    [components]
  )

  const monitoringComponents = useMemo(() =>
    components.filter(c => c.category === 'monitoring'),
    [components]
  )

  const infrastructureComponents = useMemo(() =>
    components.filter(c => c.category === 'infrastructure'),
    [components]
  )

  const loadComponents = async () => {
    setLoading(true)
    setError(null)

    try {
      const response = await store.listComponents()
      setComponents(response.components)
    } catch (err: any) {
      console.error('Failed to load optional components:', err)
      setError('Failed to load optional components. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const startRun = (component: OptionalComponent, verb: 'Installing' | 'Uninstalling', deploymentId: string) => {
    setRun({
      id: deploymentId,
      title: `${verb} ${component.display_name}`,
      component,
      status: 'running',
      current_step: null,
      steps_done: 0,
      reason: null,
    })
    setLogLines(null)
  }

  const handleInstall = async (component: OptionalComponent) => {
    try {
      const response = await store.installComponent(component.name, {})
      startRun(component, 'Installing', response.deployment_id)
    } catch (err: any) {
      alert(`Failed to install ${component.display_name}: ${err.response?.data?.detail ?? err.message}`)
    }
  }

  const handleUninstall = async (component: OptionalComponent) => {
    if (!confirm(`Are you sure you want to uninstall ${component.display_name}?`)) {
      return
    }
    try {
      const response = await store.uninstallComponent(component.name)
      startRun(component, 'Uninstalling', response.deployment_id)
    } catch (err: any) {
      alert(`Failed to uninstall ${component.display_name}: ${err.response?.data?.detail ?? err.message}`)
    }
  }

  // The run is state on the server: poll its status until it ends.
  useEffect(() => {
    if (!run || (run.status !== 'running' && run.status !== 'pending')) return
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
        if (data.status !== 'running' && data.status !== 'pending') {
          loadComponents()
        }
      } catch (err) {
        console.error('Failed to read the run status:', err)
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

  useEffect(() => {
    loadComponents()
  }, [])

  return (
    <TkPageWrapper description="Extend your Thinkube installation with optional AI, data, monitoring, and infrastructure components.">

      {/* Loading State */}
      {loading && (
        <div className="flex justify-center py-8"> {/* @allowed-inline */}
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      )}

      {/* Error State */}
      {!loading && error && (
        <TkErrorAlert>{error}</TkErrorAlert>
      )}

      {/* Component Categories */}
      {!loading && !error && (
        <div className="space-y-8"> {/* @allowed-inline */}
          {/* AI Components */}
          {aiComponents.length > 0 && (
            <div>
              <h2 className="text-2xl font-bold mb-4">AI & Machine Learning</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"> {/* @allowed-inline */}
                {aiComponents.map((component) => (
                  <ComponentCard
                    key={component.name}
                    component={component}
                    onInstall={() => handleInstall(component)}
                    onUninstall={() => handleUninstall(component)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Data Components */}
          {dataComponents.length > 0 && (
            <div>
              <h2 className="text-2xl font-bold mb-4">Data & Storage</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"> {/* @allowed-inline */}
                {dataComponents.map((component) => (
                  <ComponentCard
                    key={component.name}
                    component={component}
                    onInstall={() => handleInstall(component)}
                    onUninstall={() => handleUninstall(component)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Monitoring Components */}
          {monitoringComponents.length > 0 && (
            <div>
              <h2 className="text-2xl font-bold mb-4">Monitoring & Observability</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"> {/* @allowed-inline */}
                {monitoringComponents.map((component) => (
                  <ComponentCard
                    key={component.name}
                    component={component}
                    onInstall={() => handleInstall(component)}
                    onUninstall={() => handleUninstall(component)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Infrastructure Components */}
          {infrastructureComponents.length > 0 && (
            <div>
              <h2 className="text-2xl font-bold mb-4">Infrastructure & Platform</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"> {/* @allowed-inline */}
                {infrastructureComponents.map((component) => (
                  <ComponentCard
                    key={component.name}
                    component={component}
                    onInstall={() => handleInstall(component)}
                    onUninstall={() => handleUninstall(component)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* The run in progress, or its outcome */}
      {run && (
        <TkCard className="mt-8">
          <TkCardContent className="pt-6 space-y-3">
            <div className="flex items-center gap-2">
              {(run.status === 'running' || run.status === 'pending') && (
                <Loader2 className="h-5 w-5 animate-spin text-primary" />
              )}
              {run.status === 'success' && <CheckCircle2 className="h-5 w-5 text-green-600" />}
              {run.status !== 'running' && run.status !== 'pending' && run.status !== 'success' && (
                <XCircle className="h-5 w-5 text-destructive" />
              )}
              <h3 className="text-lg font-semibold">{run.title}</h3>
            </div>
            {(run.status === 'running' || run.status === 'pending') && (
              <p className="text-sm text-muted-foreground">
                {run.current_step ? `Step ${run.steps_done}: ${run.current_step}` : 'Starting...'}
              </p>
            )}
            {run.status === 'success' && (
              <p className="text-sm">{run.component.display_name}: done, {run.steps_done} steps.</p>
            )}
            {run.status !== 'running' && run.status !== 'pending' && run.status !== 'success' && (
              <TkErrorAlert>
                {run.status}{run.reason ? `: ${run.reason}` : ''}. The component can be submitted again.
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
              {run.status !== 'running' && run.status !== 'pending' && (
                <TkButton intent="secondary" size="sm" onClick={() => { setRun(null); setLogLines(null) }}>
                  Close
                </TkButton>
              )}
            </div>
          </TkCardContent>
        </TkCard>
      )}
    </TkPageWrapper>
  )
}
