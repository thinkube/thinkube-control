import { useEffect } from 'react'
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent, TkCardFooter } from 'thinkube-style/components/cards-data'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { Loader2, RefreshCw, Zap, ZapOff, ExternalLink } from 'lucide-react'
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

function KnativeServiceCard({ service }: { service: KnativeService }) {
  const isActive = service.status === 'Ready' && service.current_replicas > 0
  const isScaledToZero = service.status === 'Ready' && service.current_replicas === 0
  const borderClass = service.status === 'NotReady'
    ? 'border-destructive/50'
    : isActive
    ? 'border-[var(--color-success)]/30'
    : ''

  const healthDotClass = isActive
    ? 'bg-[var(--color-success)]'
    : isScaledToZero
    ? 'bg-muted-foreground'
    : service.status === 'NotReady'
    ? 'bg-[var(--color-error)]'
    : 'bg-[var(--color-warning)]'

  return (
    <TkCard className={`h-full ${borderClass} flex flex-col`}>
      <TkCardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              {isActive ? (
                <Zap className="w-5 h-5 text-[var(--color-success)]" />
              ) : (
                <ZapOff className="w-5 h-5 text-muted-foreground" />
              )}
              <TkCardTitle className="text-xl">{service.name}</TkCardTitle>
              <div className={`h-2 w-2 rounded-full ${healthDotClass}`} />
            </div>
            <p className="text-sm text-muted-foreground">{service.namespace}</p>
          </div>
          <StatusBadge status={service.status} replicas={service.current_replicas} />
        </div>
      </TkCardHeader>

      <TkCardContent className="pb-3 flex-grow">
        {/* Badges */}
        <div className="flex flex-wrap gap-2 mb-4">
          <TkBadge variant="secondary">Knative</TkBadge>
          <TkBadge variant="outline">{service.min_scale}-{service.max_scale} pods</TkBadge>
          {service.container_concurrency > 0 && (
            <TkBadge variant="outline">{service.container_concurrency} req/pod</TkBadge>
          )}
          {service.timeout_seconds !== 300 && (
            <TkBadge variant="outline">{service.timeout_seconds}s timeout</TkBadge>
          )}
        </div>

        {/* Details */}
        <div className="space-y-2 text-sm text-muted-foreground">
          {service.latest_revision && (
            <div className="flex justify-between">
              <span>Revision:</span>
              <span className="text-foreground">{service.latest_revision}</span>
            </div>
          )}
          {service.last_transition && (
            <div className="flex justify-between">
              <span>Last Activity:</span>
              <span className="text-foreground">{formatTime(service.last_transition)}</span>
            </div>
          )}
          {service.image && (
            <div className="flex justify-between">
              <span>Image:</span>
              <span className="text-foreground truncate max-w-[200px]" title={service.image}>
                {service.image.split('/').pop()?.split('@')[0] || service.image}
              </span>
            </div>
          )}
        </div>
      </TkCardContent>

      {service.url && (
        <TkCardFooter className="pt-3">
          <TkButton
            size="sm"
            variant="default"
            className="flex-1"
            asChild
          >
            <a href={service.url} target="_blank" rel="noopener noreferrer">
              <ExternalLink className="h-4 w-4 mr-1" />
              Open
            </a>
          </TkButton>
        </TkCardFooter>
      )}
    </TkCard>
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
    <TkPageWrapper>
      <div className="prose prose-lg mb-8">
        <h1>Knative Services</h1>
        <p className="lead">
          Serverless workloads with automatic scale-to-zero
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
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
          variant="outline"
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
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
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
