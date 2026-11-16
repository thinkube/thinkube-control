/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { TkBadge } from 'thinkube-style/components/buttons-badges';
import type { RunState } from '@/lib/mill';

/** A run's state as a badge. */
export function RunStateBadge({ state }: { state: RunState }) {
  if (state === 'running') return <TkBadge status="pending">running</TkBadge>;
  if (state === 'completed') return <TkBadge status="healthy">completed</TkBadge>;
  if (state === 'failed') return <TkBadge status="unhealthy">failed</TkBadge>;
  return <TkBadge status="warning">stopped</TkBadge>;
}
