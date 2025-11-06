import { useState } from 'react';
import { Star, ExternalLink, Info, RotateCw, Heart, Server, Code, BarChart3, Shield, Database, Cpu, FileText, Box, Grip } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkSwitch } from 'thinkube-style/components/forms-inputs';
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

  // Health status class
  const healthStatusClass = {
    healthy: 'bg-success',
    unhealthy: 'bg-destructive',
    unknown: 'bg-muted',
    disabled: 'bg-muted',
  }[healthStatus] || 'bg-muted';

  // Type badge variant
  const typeVariant = {
    core: 'default' as const,
    optional: 'secondary' as const,
    user_app: 'outline' as const,
  }[service.type] || 'outline' as const;

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

  return (
    <TkCard className="h-full">
      <TkCardHeader>
        <TkCardTitle className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            {/* Service Icon */}
            {hasCustomIcon ? (
              <TkBrandIcon
                icon={service.icon!.replace('/icons/', '').replace('.svg', '')}
                alt={service.display_name || service.name}
                size={variant === 'favorite' ? 20 : 24}
              />
            ) : IconComponent ? (
              <IconComponent className={variant === 'favorite' ? 'h-5 w-5' : 'h-6 w-6'} />
            ) : null}
            <span className={variant === 'favorite' ? 'text-sm font-semibold' : 'text-base font-semibold'}>
              {service.display_name || service.name}
            </span>
            {service.is_enabled && (
              <span className={`h-3 w-3 rounded-full ${healthStatusClass}`} />
            )}
          </div>
          {variant === 'full' && onToggleFavorite && (
            <TkButton
              variant="ghost"
              size="icon-sm"
              onClick={() => onToggleFavorite(service)}
            >
              <Star
                className={`h-4 w-4 ${service.is_favorite ? 'fill-warning text-warning' : ''}`}
              />
            </TkButton>
          )}
        </TkCardTitle>
      </TkCardHeader>

      <TkCardContent>
        {/* GPU Badge */}
        {service.gpu_count && service.gpu_count > 0 && (
          <div className="mb-3">
            <TkBadge variant="default" className="gap-2">
              <Grip className="h-4 w-4" />
              <span className="font-semibold">
                {service.gpu_count} GPU{service.gpu_count > 1 ? 's' : ''}
              </span>
              {service.gpu_nodes && service.gpu_nodes.length > 0 && variant === 'full' && !compact && (
                <span className="text-xs opacity-80">
                  ({service.gpu_nodes.join(', ')})
                </span>
              )}
            </TkBadge>
          </div>
        )}

        {/* Description - only in full variant and non-compact */}
        {variant === 'full' && !compact && service.description && (
          <p className="text-sm text-muted-foreground mb-3">{service.description}</p>
        )}

        {/* Service Info - only in full variant and non-compact */}
        {variant === 'full' && !compact && (
          <div className="flex gap-2 mb-3">
            <TkBadge variant={typeVariant}>
              {service.type === 'core' ? 'Core' : service.type === 'optional' ? 'Optional' : 'User App'}
            </TkBadge>
            {service.category && (
              <TkBadge variant="outline">{service.category}</TkBadge>
            )}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center justify-between gap-2 mt-auto">
          <div className="flex gap-1">
            {/* Open Service */}
            {service.is_enabled && isWebUrl(service.url) && (
              <TkButton
                variant="default"
                size={variant === 'favorite' ? 'icon-sm' : 'icon'}
                asChild
              >
                <a href={service.url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="h-4 w-4" />
                </a>
              </TkButton>
            )}

            {/* Details */}
            {onShowDetails && (
              <TkButton
                variant="ghost"
                size={variant === 'favorite' ? 'icon-sm' : 'icon'}
                onClick={() => onShowDetails(service)}
              >
                <Info className="h-4 w-4" />
              </TkButton>
            )}

            {/* Restart - only in full variant */}
            {variant === 'full' && service.is_enabled && onRestart && (
              <TkButton
                variant="ghost"
                size="icon"
                onClick={handleRestart}
                disabled={restarting}
              >
                <RotateCw className={`h-4 w-4 ${restarting ? 'animate-spin' : ''}`} />
              </TkButton>
            )}

            {/* Health Check - only in full variant */}
            {variant === 'full' && service.is_enabled && onHealthCheck && (
              <TkButton
                variant="ghost"
                size="icon"
                onClick={handleHealthCheck}
                disabled={checkingHealth}
              >
                <Heart className={`h-4 w-4 ${checkingHealth ? 'animate-pulse text-destructive' : ''}`} />
              </TkButton>
            )}
          </div>

          {/* Toggle switch - only in full variant if service can be disabled */}
          {variant === 'full' && service.can_be_disabled && onToggleService && (
            <div className="flex items-center gap-2">
              <span className={`text-xs ${service.is_enabled ? 'text-success' : 'text-muted-foreground'}`}>
                {service.is_enabled ? 'ON' : 'OFF'}
              </span>
              <TkSwitch
                checked={service.is_enabled}
                onCheckedChange={handleToggle}
                disabled={toggling}
              />
            </div>
          )}
        </div>
      </TkCardContent>
    </TkCard>
  );
}
