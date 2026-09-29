/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from 'react';
import { Star, ExternalLink, Info, RotateCw, Heart, Server, Code, BarChart3, Shield, Database, Cpu, FileText, Box, MoreHorizontal, Power, AlertTriangle } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent, TkCardFooter } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge, TkGpuBadge } from 'thinkube-style/components/buttons-badges';
import { TkTooltip } from 'thinkube-style/components/modals-overlays';
import { TkBrandIcon } from 'thinkube-style/components/brand-icons';
import {
  TkDropdownMenuRoot,
  TkDropdownMenuTrigger,
  TkDropdownMenuContent,
  TkDropdownMenuItem,
} from 'thinkube-style/components/navigation';
import type { Service } from '@/stores/useServicesStore';

interface ServiceCardProps {
  service: Service;
  variant?: 'full' | 'favorite';
  onToggleFavorite?: (service: Service) => void;
  onShowDetails?: (service: Service) => void;
  onRestart?: (service: Service) => void;
  onToggleService?: (service: Service, enabled: boolean) => void;
  onHealthCheck?: (service: Service) => void;
}

export function ServiceCard({
  service,
  variant = 'full',
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

  const statusBadgeStatus = {
    healthy: 'healthy' as const,
    unhealthy: 'unhealthy' as const,
    unknown: 'warning' as const,
    disabled: 'pending' as const,
    idle: 'pending' as const,
  }[healthStatus] || 'pending' as const;

  const statusLabel = {
    healthy: 'Healthy',
    unhealthy: 'Unhealthy',
    unknown: 'Unknown',
    disabled: 'Disabled',
    idle: 'Idle',
  }[healthStatus] || 'Unknown';

  // Type badge category
  const typeBadgeCategory = {
    core: 'core' as const,
    optional: 'optional' as const,
    user_app: 'user' as const,
  }[service.type] || 'user' as const;

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
            <TkBadge status={statusBadgeStatus} className="text-xs">{statusLabel}</TkBadge>
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
                <TkButton size="icon" intent="ghost" className="h-7 w-7" asChild>
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
                  intent="ghost"
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

  const name = service.display_name || service.name;
  const canOpen = service.is_enabled && isWebUrl(service.url);
  const canRestart = service.is_enabled && !!onRestart;
  const canCheckHealth = service.is_enabled && !!onHealthCheck;
  const canToggle = service.can_be_disabled && !!onToggleService;
  const typeLabel = service.type === 'core' ? 'Core component' : service.type === 'optional' ? 'Optional component' : 'Your app';
  const typeColor = `var(--service-type-${service.type === 'core' ? 'core' : service.type === 'optional' ? 'optional' : 'user'})`;
  // The left bar carries the state; a state that needs attention also gets
  // an icon, so it does not rest on colour alone.
  const stateBar = {
    healthy: 'border-l-success',
    unhealthy: 'border-l-destructive',
    unknown: 'border-l-warning',
  }[healthStatus] || 'border-l-muted-foreground';

  // Full variant: identity (the logo's colour is the type), the corner
  // (GPUs, favourite, occasional actions) and two buttons. The description
  // shows on hover over the identity; the state is the left bar.
  return (
    <TkCard className={`h-full flex flex-col border-l-4 ${stateBar}`}>
      <span className="sr-only">Status: {statusLabel}. {typeLabel}.</span>
      <TkCardHeader className="pb-3">
        {/* Identity: the name has the full width; GPUs end the second line */}
        <TkTooltip content={service.description || name}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="shrink-0" title={typeLabel}>
              {hasCustomIcon ? (
                <TkBrandIcon
                  icon={service.icon!.replace('/icons/', '').replace('.svg', '')}
                  alt={typeLabel}
                  size={40}
                  color={typeColor}
                />
              ) : IconComponent ? (
                <IconComponent className="h-10 w-10" style={{ color: typeColor }} aria-label={typeLabel} />
              ) : null}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 min-w-0">
                <button
                  type="button"
                  className="text-lg font-semibold leading-tight truncate text-left text-[color:var(--heading)] hover:underline"
                  onClick={() => onShowDetails?.(service)}
                >
                  {name}
                </button>
                {healthStatus !== 'healthy' && (
                  <AlertTriangle className="h-4 w-4 shrink-0 text-muted-foreground" aria-label={statusLabel}>
                    <title>{statusLabel}</title>
                  </AlertTriangle>
                )}
              </div>
              <div className="flex items-center gap-2 min-w-0 min-h-5">
                <p className="text-sm text-muted-foreground truncate flex-1 min-w-0">
                  {service.powered_by ? (
                    <>Powered by <span className="font-medium text-foreground">{service.powered_by}</span></>
                  ) : '\u00a0'}
                </p>
                {service.gpu_count && service.gpu_count > 0 && (
                  <span className="shrink-0"><TkGpuBadge gpuCount={service.gpu_count} size="sm" /></span>
                )}
              </div>
            </div>
          </div>
        </TkTooltip>
      </TkCardHeader>

      {/* Actions: the two everyday ones labelled, the rest in the corner */}
      <TkCardFooter className="mt-auto flex items-center gap-2">
        {canOpen ? (
          <TkButton size="sm" className="h-7 px-2.5 text-xs" asChild>
            <a href={service.url} target="_blank" rel="noopener noreferrer">
              Open <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </TkButton>
        ) : (
          <TkButton size="sm" className="h-7 px-2.5 text-xs" disabled>
            Open <ExternalLink className="h-3.5 w-3.5" />
          </TkButton>
        )}
        <TkButton size="sm" intent="secondary" className="h-7 px-2.5 text-xs" disabled={!onShowDetails} onClick={() => onShowDetails?.(service)}>
          Details
        </TkButton>
        <div className="ml-auto flex items-center -mr-2">
          {onToggleFavorite && (
            <TkTooltip content={service.is_favorite ? 'Remove from favorites' : 'Add to favorites'}>
              <TkButton intent="ghost" size="icon" className="h-8 w-8" onClick={() => onToggleFavorite(service)} aria-label="Favorite">
                <Star className={`h-4 w-4 ${service.is_favorite ? 'fill-warning text-warning' : ''}`} />
              </TkButton>
            </TkTooltip>
          )}
          <TkDropdownMenuRoot>
            <TkDropdownMenuTrigger asChild>
              <TkButton intent="ghost" size="icon" className="h-8 w-8" aria-label="More actions">
                <MoreHorizontal className="h-4 w-4" />
              </TkButton>
            </TkDropdownMenuTrigger>
            <TkDropdownMenuContent align="end" className="w-48">
              <TkDropdownMenuItem disabled={!canRestart || restarting} onSelect={handleRestart}>
                <RotateCw className={`mr-2 h-4 w-4 ${restarting ? 'animate-spin' : ''}`} />
                Restart
              </TkDropdownMenuItem>
              <TkDropdownMenuItem disabled={!canCheckHealth || checkingHealth} onSelect={handleHealthCheck}>
                <Heart className={`mr-2 h-4 w-4 ${checkingHealth ? 'animate-pulse' : ''}`} />
                Check health
              </TkDropdownMenuItem>
              {canToggle && (
                <TkDropdownMenuItem disabled={toggling} onSelect={() => handleToggle(!service.is_enabled)}>
                  <Power className="mr-2 h-4 w-4" />
                  {service.is_enabled ? 'Disable' : 'Enable'}
                </TkDropdownMenuItem>
              )}
            </TkDropdownMenuContent>
          </TkDropdownMenuRoot>
        </div>
      </TkCardFooter>
    </TkCard>
  );
}
