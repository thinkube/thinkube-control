/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { CheckCircle2, CircleSlash, Clock, ListOrdered, Loader2, PauseCircle, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkBadge } from 'thinkube-style/components/buttons-badges';
import type { RunState } from '@/lib/forge';

/** A run's place in the training queue, in words and with an icon, never by colour alone. */
export function RunStateBadge({ state, preemptedBy }: { state: RunState; preemptedBy?: string | null }) {
  const { t } = useTranslation();
  const label = state === 'paused' && preemptedBy ? t('queue.preempted', { run: preemptedBy }) : t(`queue.state.${state}`);
  if (state === 'preprocessing' || state === 'planning' || state === 'running' || state === 'stopping') {
    return (
      <TkBadge status="pending" className="gap-1">
        <Loader2 className="h-3 w-3 animate-spin" />
        {label}
      </TkBadge>
    );
  }
  if (state === 'planned' || state === 'queued') {
    return (
      <TkBadge appearance="outlined" className="gap-1">
        {state === 'queued' ? <ListOrdered className="h-3 w-3" /> : <Clock className="h-3 w-3" />}
        {label}
      </TkBadge>
    );
  }
  if (state === 'paused') {
    return (
      <TkBadge status="warning" className="gap-1">
        <PauseCircle className="h-3 w-3" />
        {label}
      </TkBadge>
    );
  }
  if (state === 'completed') {
    return (
      <TkBadge status="healthy" className="gap-1">
        <CheckCircle2 className="h-3 w-3" />
        {label}
      </TkBadge>
    );
  }
  if (state === 'cancelled') {
    return (
      <TkBadge appearance="muted" className="gap-1">
        <CircleSlash className="h-3 w-3" />
        {label}
      </TkBadge>
    );
  }
  return (
    <TkBadge status="unhealthy" className="gap-1">
      <XCircle className="h-3 w-3" />
      {label}
    </TkBadge>
  );
}
