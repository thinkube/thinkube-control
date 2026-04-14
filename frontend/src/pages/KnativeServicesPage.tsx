import { useEffect } from 'react'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import {
  TkTable,
  TkTableBody,
  TkTableCell,
  TkTableHead,
  TkTableHeader,
  TkTableRow,
} from 'thinkube-style/components/tables'
import { Loader2, RefreshCw, Zap, ZapOff } from 'lucide-react'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { useKnativeServicesStore, type KnativeService } from '../stores/useKnativeServicesStore'

function StatusBadge({ status, replicas }: { status: string; replicas: number }) {
  if (status === 'Ready' && replicas > 0) {
    return <TkBadge variant="default">Active ({replicas})</TkBadge>
  }
  if (status === 'Ready' && replicas === 0) {
    return <TkBadge variant="secondary">Scaled to Zero</TkBadge>
  }
  if (status === 'NotReady') {
    return <TkBadge variant="destructive">Not Ready</TkBadge>
  }
  return <TkBadge variant="outline">Unknown</TkBadge>
}

function ScalingInfo({ service }: { service: KnativeService }) {
  const parts = []
  parts.push(`${service.min_scale}-${service.max_scale} pods`)
  if (service.container_concurrency > 0) {
    parts.push(`${service.container_concurrency} req/pod`)
  }
  if (service.timeout_seconds !== 300) {
    parts.push(`${service.timeout_seconds}s timeout`)
  }
  return <span className="text-sm text-muted-foreground">{parts.join(' / ')}</span>
}

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
    <div className="space-y-6 p-6">
      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <TkCard>
          <TkCardContent className="p-4">
            <div className="flex items-center gap-3">
              <Zap className="h-5 w-5 text-green-500" />
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

      {/* Services table */}
      <TkCard>
        <TkCardContent>
          <div className="flex items-center justify-between p-4 pb-0">
            <h3 className="text-lg font-semibold">Knative Services</h3>
            <TkButton
              variant="outline"
              size="sm"
              onClick={() => fetchServices()}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              <span className="ml-2">Refresh</span>
            </TkButton>
          </div>

          {error && (
            <div className="p-4 text-destructive text-sm">
              Failed to load services: {error}
            </div>
          )}

          {loading && services.length === 0 ? (
            <div className="flex items-center justify-center p-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              <span className="ml-2 text-muted-foreground">Loading services...</span>
            </div>
          ) : services.length === 0 ? (
            <div className="p-8 text-center text-muted-foreground">
              No Knative services deployed yet.
            </div>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Name</TkTableHead>
                  <TkTableHead>Namespace</TkTableHead>
                  <TkTableHead>Status</TkTableHead>
                  <TkTableHead>Scaling</TkTableHead>
                  <TkTableHead>Revision</TkTableHead>
                  <TkTableHead>Last Activity</TkTableHead>
                  <TkTableHead>URL</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {services.map((service) => (
                  <TkTableRow key={`${service.namespace}/${service.name}`}>
                    <TkTableCell className="font-medium">{service.name}</TkTableCell>
                    <TkTableCell className="text-muted-foreground">{service.namespace}</TkTableCell>
                    <TkTableCell>
                      <StatusBadge status={service.status} replicas={service.current_replicas} />
                    </TkTableCell>
                    <TkTableCell>
                      <ScalingInfo service={service} />
                    </TkTableCell>
                    <TkTableCell className="text-sm text-muted-foreground">
                      {service.latest_revision || '-'}
                    </TkTableCell>
                    <TkTableCell className="text-sm text-muted-foreground">
                      {formatTime(service.last_transition)}
                    </TkTableCell>
                    <TkTableCell>
                      {service.url ? (
                        <a
                          href={service.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-primary hover:underline truncate block max-w-[200px]"
                        >
                          {service.url.replace('https://', '')}
                        </a>
                      ) : (
                        <span className="text-sm text-muted-foreground">-</span>
                      )}
                    </TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          )}
        </TkCardContent>
      </TkCard>
    </div>
  )
}
