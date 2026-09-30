/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect } from 'react'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { Loader2, RefreshCw, Zap, ZapOff, ExternalLink } from 'lucide-react'
import { useKnativeServicesStore, type KnativeService } from '../stores/useKnativeServicesStore'
import { AppCard, type AppCardState } from '@/components/AppCard'

function formatTime(isoString: string | null) {
  if (!isoString) return '-'
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays}d ago`
}

// A running service is healthy and one scaled to zero is idle, as on the
// dashboard cards.
function knativeState(service: KnativeService): { state: AppCardState; label: string; summary: string } {
  if (service.status === 'Ready' && service.current_replicas > 0) {
    const pods = `${service.current_replicas} pod${service.current_replicas > 1 ? 's' : ''}`
    return { state: 'healthy', label: 'Active', summary: `${pods} running` }
  }
  if (service.status === 'Ready') {
    return { state: 'idle', label: 'Scaled to zero', summary: 'Scaled to zero' }
  }
  if (service.status === 'NotReady') {
    return { state: 'unhealthy', label: 'Not ready', summary: service.ready_condition ?? 'Not ready' }
  }
  return { state: 'unknown', label: 'Unknown', summary: `Status: ${service.status}` }
}

function KnativeServiceCard({ service }: { service: KnativeService }) {
  const { state, label, summary } = knativeState(service)
  const openButton = (
    <>Open <ExternalLink className="h-3.5 w-3.5" /></>
  )

  return (
    <AppCard
      state={state}
      statusLabel={label}
      srDescription="Knative service."
      label={service.namespace}
      icon={<Zap className="h-10 w-10 text-muted-foreground" aria-hidden="true" />}
      name={service.name}
      subtitle={summary}
      tooltip={
        <>
          <p>Scales between {service.min_scale} and {service.max_scale} pods</p>
          {service.container_concurrency > 0 && <p>{service.container_concurrency} requests per pod</p>}
          <p>Request timeout {service.timeout_seconds}s</p>
          {service.latest_revision && <p>Revision {service.latest_revision}</p>}
          {service.last_transition && <p>Last activity {formatTime(service.last_transition)}</p>}
          {service.image && <p className="break-all">Image {service.image.split('/').pop()?.split('@')[0]}</p>}
        </>
      }
      actions={
        service.url ? (
          <TkButton size="sm" className="h-7 px-2.5 text-xs" asChild>
            <a href={service.url} target="_blank" rel="noopener noreferrer">{openButton}</a>
          </TkButton>
        ) : (
          <TkButton size="sm" className="h-7 px-2.5 text-xs" disabled>{openButton}</TkButton>
        )
      }
    />
  )
}

export default function KnativeServicesPage() {
  const { services, loading, error, fetchServices } = useKnativeServicesStore()

  useEffect(() => {
    fetchServices()
  }, [fetchServices])

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const interval = setInterval(fetchServices, 30000)
    return () => clearInterval(interval)
  }, [fetchServices])

  const activeCount = services.filter(s => s.current_replicas > 0).length
  const idleCount = services.filter(s => s.status === 'Ready' && s.current_replicas === 0).length

  return (
    <TkPageWrapper description="Serverless workloads with automatic scale-to-zero">

      {/* Summary cards */}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] gap-4 mb-8">
        <TkCard>
          <TkCardContent className="p-4">
            <div className="flex items-center gap-3">
              <Zap className="h-5 w-5 text-[var(--color-success)]" />
              <div>
                <p className="text-sm text-muted-foreground">Active</p>
                <p className="text-2xl font-bold">{activeCount}</p>
              </div>
            </div>
          </TkCardContent>
        </TkCard>
        <TkCard>
          <TkCardContent className="p-4">
            <div className="flex items-center gap-3">
              <ZapOff className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-sm text-muted-foreground">Scaled to Zero</p>
                <p className="text-2xl font-bold">{idleCount}</p>
              </div>
            </div>
          </TkCardContent>
        </TkCard>
        <TkCard>
          <TkCardContent className="p-4">
            <div className="flex items-center gap-3">
              <div>
                <p className="text-sm text-muted-foreground">Total Services</p>
                <p className="text-2xl font-bold">{services.length}</p>
              </div>
            </div>
          </TkCardContent>
        </TkCard>
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-bold">Services</h2>
        <TkButton
          intent="secondary"
          size="sm"
          onClick={() => fetchServices()}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
          ) : (
            <RefreshCw className="h-4 w-4 mr-2" />
          )}
          Refresh
        </TkButton>
      </div>

      {error && (
        <div className="p-4 text-destructive text-sm mb-4">
          Failed to load services: {error}
        </div>
      )}

      {loading && services.length === 0 ? (
        <div className="flex items-center justify-center p-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          <span className="ml-2 text-muted-foreground">Loading services...</span>
        </div>
      ) : services.length === 0 ? (
        <div className="p-12 text-center text-muted-foreground">
          No Knative services deployed yet.
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(18rem,1fr))] *:max-w-[22rem] gap-x-4 gap-y-6 pt-2">
          {services.map((service) => (
            <KnativeServiceCard
              key={`${service.namespace}/${service.name}`}
              service={service}
            />
          ))}
        </div>
      )}
    </TkPageWrapper>
  )
}
