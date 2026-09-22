/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { TkErrorAlert } from 'thinkube-style/components/feedback'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { ComponentCard } from '../components/ComponentCard'
import { useComponentsStore } from '../stores/useComponentsStore'
import { useRunsStore } from '../stores/useRunsStore'

interface OptionalComponent {
  name: string
  display_name: string
  category: 'ai' | 'data' | 'monitoring' | 'infrastructure'
  description: string
  icon?: string
  is_installed: boolean
  activity?: 'queued' | 'installing' | 'uninstalling' | null
  requirements: string[]
  [key: string]: any
}

export default function OptionalComponentsPage() {
  const store = useComponentsStore()
  const { active, queued, recent, openDialog } = useRunsStore()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [components, setComponents] = useState<OptionalComponent[]>([])

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

  // A refresh after a run changes state keeps the page in place; only the
  // first load shows the spinner.
  const loadComponents = async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError(null)

    try {
      const response = await store.listComponents()
      setComponents(response.components)
    } catch (err: any) {
      console.error('Failed to load optional components:', err)
      setError('Failed to load optional components. Please try again.')
    } finally {
      if (!quiet) setLoading(false)
    }
  }

  // Runs start one at a time from the server's queue; the runs dialog
  // follows them.
  const queuedMessage = (verb: string, component: OptionalComponent, response: any) =>
    response.queue_position && response.queue_position > 1
      ? `${verb} ${component.display_name}: queued at position ${response.queue_position}`
      : `${verb} ${component.display_name}: queued, starts next`

  const handleInstall = async (component: OptionalComponent) => {
    try {
      const response = await store.installComponent(component.name, {})
      toast.success(queuedMessage('Install', component, response))
      openDialog()
      loadComponents(true)
    } catch (err: any) {
      toast.error(`Failed to queue the install of ${component.display_name}: ${err.response?.data?.detail ?? err.message}`)
    }
  }

  const handleUninstall = async (component: OptionalComponent) => {
    if (!confirm(`Are you sure you want to uninstall ${component.display_name}?`)) {
      return
    }
    try {
      const response = await store.uninstallComponent(component.name)
      toast.success(queuedMessage('Uninstall', component, response))
      openDialog()
      loadComponents(true)
    } catch (err: any) {
      toast.error(`Failed to queue the uninstall of ${component.display_name}: ${err.response?.data?.detail ?? err.message}`)
    }
  }

  // A run starting or ending changes what the cards show.
  const queueState = `${active?.id ?? ''}:${active?.status ?? ''}:${queued.map(r => r.id).join(',')}:${recent[0]?.id ?? ''}`
  useEffect(() => {
    if (components.length) loadComponents(true)
  }, [queueState])

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

    </TkPageWrapper>
  )
}
