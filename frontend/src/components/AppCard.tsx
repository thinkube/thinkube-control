/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardFooter } from 'thinkube-style/components/cards-data';
import { TkTooltip } from 'thinkube-style/components/modals-overlays';

export type AppCardState = 'healthy' | 'idle' | 'unknown' | 'unhealthy' | 'disabled';

// The left bar carries the state: healthy green, idle blue (running with no
// work, such as a model server with no model or a service scaled to zero),
// unknown yellow, unhealthy red, disabled grey. The two states that need
// attention also get an icon, so they do not rest on colour alone; a disabled
// card is faded.
const STATE_BAR: Record<AppCardState, string> = {
  healthy: 'border-l-success',
  idle: 'border-l-info',
  unknown: 'border-l-warning',
  unhealthy: 'border-l-destructive',
  disabled: 'border-l-muted-foreground',
};

interface AppCardProps {
  state: AppCardState;
  statusLabel: string;
  /** Read by screen readers after the status. */
  srDescription?: string;
  /** Written into the top border line. */
  label?: string;
  icon: ReactNode;
  name: string;
  onNameClick?: () => void;
  /** The second line under the name. */
  subtitle?: ReactNode;
  /** A small tag at the end of the second line. */
  tag?: ReactNode;
  /** Shown on hover over the identity, below the status. */
  tooltip?: ReactNode;
  /** The labelled buttons at the start of the footer. */
  actions: ReactNode;
  /** The icon buttons at the end of the footer. */
  corner?: ReactNode;
}

export function AppCard({
  state,
  statusLabel,
  srDescription,
  label,
  icon,
  name,
  onNameClick,
  subtitle,
  tag,
  tooltip,
  actions,
  corner,
}: AppCardProps) {
  const needsAttention = state === 'unhealthy' || state === 'unknown';

  return (
    <TkCard className={`relative h-full flex flex-col border-l-4 ${STATE_BAR[state]}`}>
      <span className="sr-only">Status: {statusLabel}.{srDescription ? ` ${srDescription}` : ''}</span>
      {/* The label is written into the top border line; its background is
          the page above the line and the card below it. */}
      {label && (
        <span
          className="absolute -top-2 left-4 px-1.5 text-[11px] leading-4 font-medium uppercase tracking-wide text-muted-foreground"
          style={{ background: 'linear-gradient(to bottom, var(--background) 50%, var(--card) 50%)' }}
        >
          {label}
        </span>
      )}
      <TkCardHeader className="px-5 pb-3">
        {/* Identity: the name has the full width; the tag ends the second line */}
        <TkTooltip
          content={
            <div className="space-y-1">
              <p className="font-medium">{statusLabel}</p>
              {tooltip}
            </div>
          }
        >
          <div className={`flex items-center gap-2.5 min-w-0 ${state === 'disabled' ? 'opacity-50' : ''}`}>
            <div className="shrink-0 flex">{icon}</div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 min-w-0">
                {onNameClick ? (
                  <button
                    type="button"
                    className="text-lg font-semibold leading-tight truncate text-left text-[color:var(--heading)] hover:underline"
                    onClick={onNameClick}
                  >
                    {name}
                  </button>
                ) : (
                  <span className="text-lg font-semibold leading-tight truncate text-[color:var(--heading)]">
                    {name}
                  </span>
                )}
                {needsAttention && (
                  <AlertTriangle className="h-4 w-4 shrink-0 text-muted-foreground" aria-label={statusLabel}>
                    <title>{statusLabel}</title>
                  </AlertTriangle>
                )}
              </div>
              <div className="flex items-center gap-1.5 min-w-0 h-5">
                <p className="text-sm text-muted-foreground truncate flex-1 min-w-0">
                  {subtitle ?? ' '}
                </p>
                {tag && (
                  <span className="shrink-0 border border-warning bg-warning/25 px-1 text-[10px] font-semibold leading-4">
                    {tag}
                  </span>
                )}
              </div>
            </div>
          </div>
        </TkTooltip>
      </TkCardHeader>

      {/* Actions: the everyday ones labelled, the rest in the corner */}
      <TkCardFooter className="mt-auto px-5 flex items-center gap-2">
        {actions}
        {corner && <div className="ml-auto flex items-center -mr-2">{corner}</div>}
      </TkCardFooter>
    </TkCard>
  );
}
