/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { CheckCircle2, CircleSlash, Loader2, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { TkBadge } from 'thinkube-style/components/buttons-badges';
import type { RunStatus } from '@/lib/forge';

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const { t } = useTranslation();
  const label = t(`run.status.${status}`);
  if (status === 'starting' || status === 'running') {
    return (
      <TkBadge status="pending" className="gap-1">
        <Loader2 className="h-3 w-3 animate-spin" />
        {label}
      </TkBadge>
    );
  }
  if (status === 'completed') {
    return (
      <TkBadge status="healthy" className="gap-1">
        <CheckCircle2 className="h-3 w-3" />
        {label}
      </TkBadge>
    );
  }
  if (status === 'cancelled') {
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
