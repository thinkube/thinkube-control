import { useState } from 'react';
import { Star, ExternalLink, Info, RotateCw, Heart, Server, Code, BarChart3, Shield, Database, Cpu, FileText, Box } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent, TkCardFooter } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge, TkGpuBadge } from 'thinkube-style/components/buttons-badges';
import { TkSwitch } from 'thinkube-style/components/forms-inputs';
import { TkTooltip } from 'thinkube-style/components/modals-overlays';
import { TkBrandIcon } from 'thinkube-style/components/brand-icons';
import type { Service } from '@/stores/useServicesStore';

interface ServiceCardProps {
  service: Service;
  variant?: 'full' | 'favorite';
  compact?: boolean;
  onToggleFavorite?: (service: Service) => void;
  onShowDetails?: (service: Service) => void;
  onRestart?: (service: Service) => void;
  onToggleService?: (service: Service, enabled: boolean) => void;
  onHealthCheck?: (service: Service) => void;
}

export function ServiceCard({
  service,
  variant = 'full',
  compact = false,
  onToggleFavorite,
  onShowDetails,
  onRestart,
  onToggleService,
  onHealthCheck,
}: ServiceCardProps) {
  const [toggling, setToggling] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);

  // Determine health status
  const healthStatus = !service.is_enabled
    ? 'disabled'
    : service.latest_health?.status || 'unknown';

  // Health status for display
  const statusColor = {
    healthy: 'var(--color-success)',
    unhealthy: 'var(--color-error)',
    unknown: 'var(--color-warning)',
    disabled: 'var(--muted-foreground)',
  }[healthStatus] || 'var(--muted-foreground)';

  const statusBadgeVariant = {
    healthy: 'success' as const,
    unhealthy: 'destructive' as const,
    unknown: 'warning' as const,
    disabled: 'secondary' as const,
  }[healthStatus] || 'secondary' as const;

  const statusLabel = {
    healthy: 'Healthy',
    unhealthy: 'Unhealthy',
    unknown: 'Unknown',
    disabled: 'Disabled',
  }[healthStatus] || 'Unknown';

  // Type badge variant
  const typeVariant = {
    core: 'default' as const,
    optional: 'secondary' as const,
    user_app: 'outline' as const,
  }[service.type] || 'outline' as const;

  // Border styling based on health
  const borderClass = healthStatus === 'healthy'
    ? 'border-primary/20'
    : healthStatus === 'unhealthy'
    ? 'border-destructive/50'
    : '';

  // Handle toggle service
  const handleToggle = async (checked: boolean) => {
    if (!onToggleService) return;
    setToggling(true);
    try {
      await onToggleService(service, checked);
    } finally {
      setToggling(false);
    }
  };

  // Handle restart
  const handleRestart = async () => {
    if (!onRestart) return;
    setRestarting(true);
    try {
      await onRestart(service);
    } finally {
      setTimeout(() => setRestarting(false), 1000);
    }
  };

  // Handle health check
  const handleHealthCheck = async () => {
    if (!onHealthCheck) return;
    setCheckingHealth(true);
    try {
      await onHealthCheck(service);
    } finally {
      setCheckingHealth(false);
    }
  };

  // Check if URL is web accessible
  const isWebUrl = (url?: string) => {
    if (!url) return false;
    if (!url.startsWith('http://') && !url.startsWith('https://')) return false;
    if (url.includes('.svc.cluster.local') || url.includes('internal')) return false;
    return true;
  };

  // Get icon component based on category or service type
  const getIconComponent = () => {
    // If service has custom icon path, return null (will use TkBrandIcon)
    if (service.icon && service.icon.startsWith('/')) {
      return null;
    }

    // Map category to lucide icon
    const categoryIconMap: Record<string, any> = {
      infrastructure: Server,
      development: Code,
      monitoring: BarChart3,
      security: Shield,
      storage: Database,
      ai: Cpu,
      documentation: FileText,
      application: Box,
    };

    return categoryIconMap[service.category?.toLowerCase() || ''] || Server;
  };

  const IconComponent = getIconComponent();
  const hasCustomIcon = service.icon && service.icon.startsWith('/');

  // Favorite variant - compact design
  if (variant === 'favorite') {
    return (
      <TkCard className={`h-full ${service.is_favorite ? 'border-accent' : ''}`}>
        <TkCardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {hasCustomIcon ? (
                <TkBrandIcon
                  icon={service.icon!.replace('/icons/', '').replace('.svg', '')}
                  alt={service.display_name || service.name}
                  size={16}
                />
              ) : IconComponent ? (
                <IconComponent className="h-4 w-4" />
              ) : null}
              <TkCardTitle className="text-base">{service.display_name || service.name}</TkCardTitle>
            </div>
            <TkBadge variant={statusBadgeVariant} className="text-xs">{statusLabel}</TkBadge>
          </div>
        </TkCardHeader>
        <TkCardContent className="pb-2">
          {/* GPU Badge */}
          {service.gpu_count && service.gpu_count > 0 && (
            <div className="mb-2">
              <TkGpuBadge gpuCount={service.gpu_count} size="sm" />
            </div>
          )}

          <div className="flex gap-1">
            {/* Open Service */}
            {service.is_enabled && isWebUrl(service.url) && (
              <TkTooltip content="Open service">
                <TkButton size="icon" variant="ghost" className="h-7 w-7" asChild>
                  <a href={service.url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </TkButton>
              </TkTooltip>
            )}

            {/* Details */}
            {onShowDetails && (
              <TkTooltip content="View details">
                <TkButton
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7"
                  onClick={() => onShowDetails(service)}
                >
                  <Info className="h-3 w-3" />
                </TkButton>
              </TkTooltip>
            )}
          </div>
        </TkCardContent>
      </TkCard>
    );
  }

  // Full variant - elegant design with footer
  return (
    <TkCard className={`h-full ${borderClass}`}>
      <TkCardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              {hasCustomIcon ? (
                <TkBrandIcon
                  icon={service.icon!.replace('/icons/', '').replace('.svg', '')}
                  alt={service.display_name || service.name}
                  size={20}
                />
              ) : IconComponent ? (
                <IconComponent className={`w-5 h-5 ${healthStatus === 'unhealthy' ? 'opacity-50' : ''}`} />
              ) : null}
              <h3 className="text-xl font-semibold">{service.display_name || service.name}</h3>
              <TkTooltip
                content={
                  healthStatus === 'healthy'
                    ? 'All health checks passing'
                    : healthStatus === 'unhealthy'
                    ? 'Service is not responding'
                    : healthStatus === 'disabled'
                    ? 'Service is disabled'
                    : 'Health status unknown'
                }
              >
                <div className={`h-2 w-2 rounded-full ${
                  healthStatus === 'healthy'
                    ? 'bg-[var(--color-success)]'
                    : healthStatus === 'unhealthy'
                    ? 'bg-[var(--color-error)]'
                    : healthStatus === 'unknown'
                    ? 'bg-[var(--color-warning)]'
                    : 'bg-muted-foreground'
                }`} />
              </TkTooltip>
            </div>
            {!compact && service.description && (
              <p className="text-sm text-muted-foreground">{service.description}</p>
            )}
          </div>
          <div className="flex items-start gap-2">
            <TkBadge variant={statusBadgeVariant}>{statusLabel}</TkBadge>
            {onToggleFavorite && (
              <TkTooltip content={service.is_favorite ? 'Remove from favorites' : 'Add to favorites'}>
                <TkButton
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => onToggleFavorite(service)}
                >
                  <Star
                    className={`h-4 w-4 ${service.is_favorite ? 'fill-warning text-warning' : ''}`}
                  />
                </TkButton>
              </TkTooltip>
            )}
          </div>
        </div>
      </TkCardHeader>

      <TkCardContent className="pb-3">
        {/* Badges */}
        <div className="flex flex-wrap gap-2 mb-4">
          <TkBadge variant={typeVariant}>
            {service.type === 'core' ? 'Core' : service.type === 'optional' ? 'Optional' : 'User App'}
          </TkBadge>
          {service.category && (
            <TkBadge variant="outline">{service.category}</TkBadge>
          )}
          {service.gpu_count && service.gpu_count > 0 && (
            <TkGpuBadge gpuCount={service.gpu_count} />
          )}
        </div>

        {/* Metrics */}
        {!compact && service.latest_health && (
          <div className="space-y-2 text-sm text-muted-foreground">
            {service.latest_health.pod_status && (
              <div className="flex justify-between">
                <span>Pods:</span>
                <span className={
                  service.latest_health.pod_status.includes('Running')
                    ? 'text-[var(--color-success)]'
                    : 'text-[var(--color-error)]'
                }>
                  {service.latest_health.pod_status}
                </span>
              </div>
            )}
            {service.latest_health.last_checked && (
              <div className="flex justify-between">
                <span>Last Checked:</span>
                <span className="text-foreground">
                  {new Date(service.latest_health.last_checked).toLocaleString()}
                </span>
              </div>
            )}
          </div>
        )}
      </TkCardContent>

      <TkCardFooter className="flex-col gap-3 pt-3">
        {/* Actions */}
        <div className="flex gap-2 w-full">
          {service.is_enabled && isWebUrl(service.url) && (
            <TkTooltip content="Open service">
              <TkButton
                size="sm"
                variant="default"
                className="flex-1"
                asChild
              >
                <a href={service.url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="h-4 w-4" />
                </a>
              </TkButton>
            </TkTooltip>
          )}

          {onShowDetails && (
            <TkTooltip content="View details">
              <TkButton
                size="sm"
                variant="outline"
                className="flex-1"
                onClick={() => onShowDetails(service)}
              >
                <Info className="h-4 w-4" />
              </TkButton>
            </TkTooltip>
          )}

          {service.is_enabled && onRestart && (
            <TkTooltip content="Restart service">
              <TkButton
                size="sm"
                variant="outline"
                className="flex-1"
                onClick={handleRestart}
                disabled={restarting}
              >
                <RotateCw className={`h-4 w-4 ${restarting ? 'animate-spin' : ''}`} />
              </TkButton>
            </TkTooltip>
          )}

          {service.is_enabled && onHealthCheck && (
            <TkTooltip content="Check health">
              <TkButton
                size="sm"
                variant="outline"
                className="flex-1"
                onClick={handleHealthCheck}
                disabled={checkingHealth}
              >
                <Heart className={`h-4 w-4 ${checkingHealth ? 'animate-pulse text-destructive' : ''}`} />
              </TkButton>
            </TkTooltip>
          )}
        </div>

        {/* Toggle */}
        {service.can_be_disabled && onToggleService && (
          <div className="flex items-center justify-between w-full">
            <span className="text-sm font-medium">Service Enabled</span>
            <TkSwitch
              checked={service.is_enabled}
              onCheckedChange={handleToggle}
              disabled={toggling}
            />
          </div>
        )}
      </TkCardFooter>
    </TkCard>
  );
}
