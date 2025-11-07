import { useState, useEffect, useRef, useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import { TkErrorAlert } from 'thinkube-style/components/feedback'
import { ComponentCard } from '../components/ComponentCard'
import { PlaybookExecutor, PlaybookExecutorHandle } from '../components/PlaybookExecutor'
import { useComponentsStore } from '../stores/useComponentsStore'

interface OptionalComponent {
  name: string
  display_name: string
  category: 'ai' | 'data' | 'monitoring' | 'infrastructure'
  description: string
  icon?: string
  is_installed: boolean
  requirements: string[]
  [key: string]: any
}

export default function OptionalComponentsPage() {
  const store = useComponentsStore()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [components, setComponents] = useState<OptionalComponent[]>([])
  const [installingComponent, setInstallingComponent] = useState<OptionalComponent | null>(null)
  const [installingTitle, setInstallingTitle] = useState('')
  const [installingSuccessMessage, setInstallingSuccessMessage] = useState('')
  const playbookExecutorRef = useRef<PlaybookExecutorHandle>(null)

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

  const handleInstall = async (component: OptionalComponent) => {
    try {
      setInstallingComponent(component)
      setInstallingTitle(`Installing ${component.display_name}`)
      setInstallingSuccessMessage(`${component.display_name} has been installed successfully!`)

      const response = await store.installComponent(component.name, {})

      const wsPath = `/api/v1/ws/optional/${component.name}/install/${response.deployment_id}`
      playbookExecutorRef.current?.startExecution(wsPath)
    } catch (err: any) {
      console.error('Failed to install component:', err)
      alert(`Failed to install ${component.display_name}: ${err.message}`)
    }
  }

  const handleUninstall = async (component: OptionalComponent) => {
    if (!confirm(`Are you sure you want to uninstall ${component.display_name}?`)) {
      return
    }

    try {
      setInstallingComponent(component)
      setInstallingTitle(`Uninstalling ${component.display_name}`)
      setInstallingSuccessMessage(`${component.display_name} has been uninstalled successfully!`)

      const response = await store.uninstallComponent(component.name)

      const wsPath = `/api/v1/ws/optional/${component.name}/uninstall/${response.deployment_id}`
      playbookExecutorRef.current?.startExecution(wsPath)
    } catch (err: any) {
      console.error('Failed to uninstall component:', err)
      alert(`Failed to uninstall ${component.display_name}: ${err.message}`)
    }
  }

  const handleInstallationComplete = (result: { status: string; message?: string }) => {
    if (result.status === 'success') {
      console.log(`${installingComponent?.display_name} operation completed successfully`)

      setInstallingComponent(null)
      setInstallingTitle('')
      setInstallingSuccessMessage('')

      loadComponents()
    } else if (result.status === 'error') {
      console.error(`${installingComponent?.display_name} operation failed:`, result.message)
    }
  }

  useEffect(() => {
    loadComponents()
  }, [])

  return (
    <div className="min-h-screen bg-background p-8"> {/* @allowed-inline */}
      <div className="prose prose-lg dark:prose-invert mb-8"> {/* @allowed-inline */}
        <h1>Optional Components</h1>
        <p className="lead">
          Extend your Thinkube installation with optional AI, data, monitoring, and infrastructure components.
        </p>
      </div>

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

      {/* Playbook Executor */}
      <PlaybookExecutor
        ref={playbookExecutorRef}
        title={installingTitle}
        successMessage={installingSuccessMessage}
        onComplete={handleInstallationComplete}
      />
    </div>
  )
}
