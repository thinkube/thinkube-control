/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, type ReactNode } from 'react';
import { Star, ExternalLink, RotateCw, Heart, Server, Code, BarChart3, Shield, Database, Cpu, FileText, Box, MoreHorizontal, Power } from 'lucide-react';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkTooltip } from 'thinkube-style/components/modals-overlays';
import { TkBrandIcon } from 'thinkube-style/components/brand-icons';
import {
  TkDropdownMenuRoot,
  TkDropdownMenuTrigger,
  TkDropdownMenuContent,
  TkDropdownMenuItem,
} from 'thinkube-style/components/navigation';
import type { Service } from '@/stores/useServicesStore';
import { AppCard, type AppCardState } from './AppCard';

interface ServiceCardProps {
  service: Service;
  onToggleFavorite?: (service: Service) => void;
  onShowDetails?: (service: Service) => void;
  onRestart?: (service: Service) => void;
  onToggleService?: (service: Service, enabled: boolean) => void;
  onHealthCheck?: (service: Service) => void;
  /** Shown first in the footer corner, for cards that can be reordered. */
  dragHandle?: ReactNode;
}

export function ServiceCard({
  service,
  onToggleFavorite,
  onShowDetails,
  onRestart,
  onToggleService,
  onHealthCheck,
  dragHandle,
}: ServiceCardProps) {
  const [toggling, setToggling] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);

  // Determine health status
  const healthStatus = !service.is_enabled
    ? 'disabled'
    : service.latest_health?.status || 'unknown';

  const statusLabel = {
    healthy: 'Healthy',
    unhealthy: 'Unhealthy',
    unknown: 'Unknown',
    disabled: 'Disabled',
    idle: 'Idle',
  }[healthStatus] || 'Unknown';

  const cardState: AppCardState = (
    ['healthy', 'idle', 'unknown', 'unhealthy', 'disabled'] as const
  ).find(s => s === healthStatus) ?? 'unknown';

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

  const name = service.display_name || service.name;
  const canOpen = service.is_enabled && isWebUrl(service.url);
  const canRestart = service.is_enabled && !!onRestart;
  const canCheckHealth = service.is_enabled && !!onHealthCheck;
  const canToggle = service.can_be_disabled && !!onToggleService;
  const typeLabel = service.type === 'core' ? 'Core component' : service.type === 'optional' ? 'Optional component' : 'Your app';
  const typeColor = `var(--service-type-${service.type === 'core' ? 'core' : service.type === 'optional' ? 'optional' : 'user'})`;

  // Identity (the logo's colour is the type), the corner (GPUs, favourite,
  // occasional actions) and two buttons. The description shows on hover over
  // the identity; the state is the left bar.
  return (
    <AppCard
      state={cardState}
      statusLabel={statusLabel}
      srDescription={`${typeLabel}.`}
      label={service.category}
      icon={
        <span className="flex" title={typeLabel}>
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
        </span>
      }
      name={name}
      onNameClick={onShowDetails ? () => onShowDetails(service) : undefined}
      subtitle={service.powered_by && (
        <>Powered by <span className="font-medium text-foreground">{service.powered_by}</span></>
      )}
      tag={service.gpu_count && service.gpu_count > 0
        ? `${service.gpu_count} GPU${service.gpu_count > 1 ? 's' : ''}`
        : undefined}
      tooltip={service.description && <p>{service.description}</p>}
      actions={
        <>
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
        </>
      }
      corner={
        <>
          {dragHandle}
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
        </>
      }
    />
  );
}
