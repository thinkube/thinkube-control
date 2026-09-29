/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

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
  const lastChecked = service.latest_health?.last_checked
    ? new Date(service.latest_health.last_checked).toLocaleString()
    : null;
  const podStatus = service.latest_health?.pod_status || null;

  // Full variant. Every card stacks the same zones with the same heights, so
  // the name, status, description, facts and actions line up across a row:
  // identity, status line, description (two lines), facts (four rows) and
  // actions (four buttons and the enable switch, always in the same places).
  // Compact cards drop the description and facts.
  return (
    <TkCard className={`h-full flex flex-col ${healthStatus === 'unhealthy' ? 'border-destructive/40 border-l-4 border-l-destructive' : ''}`}>
      <TkCardHeader className="pb-3">
        {/* Identity */}
        <div className="flex items-start gap-3">
          <div className="shrink-0">
            {hasCustomIcon ? (
              <TkBrandIcon icon={service.icon!.replace('/icons/', '').replace('.svg', '')} alt={name} size={40} />
            ) : IconComponent ? (
              <IconComponent className="h-10 w-10 text-primary" />
            ) : null}
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold leading-tight truncate" title={name}>{name}</h3>
            <p className="text-sm text-muted-foreground truncate min-h-5">
              {service.powered_by ? (
                <>Powered by <span className="font-medium text-foreground">{service.powered_by}</span></>
              ) : '\u00a0'}
            </p>
          </div>
          {onToggleFavorite && (
            <TkTooltip content={service.is_favorite ? 'Remove from favorites' : 'Add to favorites'}>
              <TkButton intent="ghost" size="icon" className="shrink-0 -mr-2 -mt-1" onClick={() => onToggleFavorite(service)}>
                <Star className={`h-4 w-4 ${service.is_favorite ? 'fill-warning text-warning' : ''}`} />
              </TkButton>
            </TkTooltip>
          )}
        </div>

        {/* Status line: state first, then type and GPUs */}
        <div className="flex items-center gap-2 mt-3 h-6 overflow-hidden">
          <TkBadge status={statusBadgeStatus}>{statusLabel}</TkBadge>
          <TkBadge category={typeBadgeCategory}>
            {service.type === 'core' ? 'Core' : service.type === 'optional' ? 'Optional' : 'User App'}
          </TkBadge>
          {service.gpu_count && service.gpu_count > 0 && <TkGpuBadge gpuCount={service.gpu_count} size="sm" />}
        </div>
      </TkCardHeader>

      <TkCardContent className="pb-3 flex-grow">
        {!compact && (
          <>
            {/* Description: always two lines */}
            <p className="text-sm text-muted-foreground line-clamp-2 min-h-10" title={service.description || undefined}>
              {service.description || '\u00a0'}
            </p>

            {/* Facts: always four rows */}
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Category</dt>
              <dd className="truncate">{service.category || '—'}</dd>
              <dt className="text-muted-foreground">Version</dt>
              <dd className="truncate">{service.component_version ? `v${service.component_version}` : '—'}</dd>
              <dt className="text-muted-foreground">Pods</dt>
              <dd className="truncate flex items-center gap-1.5">
                {podStatus && (
                  <span className={`h-2 w-2 rounded-full shrink-0 ${podStatus.includes('Running') ? 'bg-success' : 'bg-destructive'}`} />
                )}
                {podStatus || '—'}
              </dd>
              <dt className="text-muted-foreground">Last check</dt>
              <dd className="truncate">{lastChecked || '—'}</dd>
            </dl>
          </>
        )}
      </TkCardContent>

      <TkCardFooter className="flex-col gap-3 pt-3 border-t border-border">
        {/* Actions: always four, in the same order; unavailable ones are disabled */}
        <div className="grid grid-cols-4 gap-2 w-full">
          <TkTooltip content={canOpen ? 'Open service' : 'No web page to open'}>
            {canOpen ? (
              <TkButton size="sm" asChild>
                <a href={service.url} target="_blank" rel="noopener noreferrer" aria-label="Open service">
                  <ExternalLink className="h-4 w-4" />
                </a>
              </TkButton>
            ) : (
              <span className="inline-flex">
                <TkButton size="sm" className="w-full" disabled aria-label="Open service">
                  <ExternalLink className="h-4 w-4" />
                </TkButton>
              </span>
            )}
          </TkTooltip>
          <TkTooltip content="View details">
            <TkButton size="sm" intent="secondary" disabled={!onShowDetails} onClick={() => onShowDetails?.(service)} aria-label="View details">
              <Info className="h-4 w-4" />
            </TkButton>
          </TkTooltip>
          <TkTooltip content="Restart service">
            <TkButton size="sm" intent="secondary" onClick={handleRestart} disabled={!canRestart || restarting} aria-label="Restart service">
              <RotateCw className={`h-4 w-4 ${restarting ? 'animate-spin' : ''}`} />
            </TkButton>
          </TkTooltip>
          <TkTooltip content="Check health">
            <TkButton size="sm" intent="secondary" onClick={handleHealthCheck} disabled={!canCheckHealth || checkingHealth} aria-label="Check health">
              <Heart className={`h-4 w-4 ${checkingHealth ? 'animate-pulse' : ''}`} />
            </TkButton>
          </TkTooltip>
        </div>

        {/* Enable switch: on every card; fixed for services that cannot be turned off */}
        <div className="flex items-center justify-between w-full">
          <span className="text-sm font-medium">Enabled</span>
          {service.can_be_disabled && onToggleService ? (
            <TkSwitch checked={service.is_enabled} onCheckedChange={handleToggle} disabled={toggling} />
          ) : (
            <TkTooltip content="This service cannot be turned off">
              <span className="inline-flex">
                <TkSwitch checked={service.is_enabled} disabled />
              </span>
            </TkTooltip>
          )}
        </div>
      </TkCardFooter>
    </TkCard>
  );
}
