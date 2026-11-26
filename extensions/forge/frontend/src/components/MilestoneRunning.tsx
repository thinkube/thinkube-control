/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useTranslation } from 'react-i18next';
import { TkProgress } from 'thinkube-style/components/feedback';
import { duration, milestoneLeft, type MilestoneRunning as Running } from '@/lib/forge';

/** A milestone answering its rows: how many of how many, since when, and the time it has left at its pace so far.
 * Compact: its share alone, for the list of runs. */
export function MilestoneRunning({ running, updated, compact = false }: {
  running: Running;
  updated: string | null | undefined;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const pct = running.rows > 0 ? Math.floor((100 * running.answered) / running.rows) : 0;
  if (compact) {
    return <span className="text-xs text-muted-foreground">{t('milestones.runningShort', { step: running.step, pct })}</span>;
  }
  const left = milestoneLeft(running, updated);
  return (
    <div className="space-y-1">
      <TkProgress value={pct} aria-label={t('milestones.runningLabel', { step: running.step })} />
      <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {t('milestones.running', {
            step: running.step.toLocaleString(), answered: running.answered.toLocaleString(),
            rows: running.rows.toLocaleString(), pct,
          })}
        </span>
        <span>
          {t('milestones.runningSince', {
            time: new Date(running.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          })}
          {left != null && ` · ${t('queue.left', { time: duration(left) })}`}
        </span>
      </div>
    </div>
  );
}
