/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useTranslation } from 'react-i18next';
import { TkProgress } from 'thinkube-style/components/feedback';
import { duration, type Run } from '@/lib/forge';

/** A run's progress from meepsquared's status file: steps done, and the time left. */
export function TrainingProgress({ run, compact = false }: { run: Run; compact?: boolean }) {
  const { t } = useTranslation();
  const p = run.progress;
  if (!p || p.total_steps == null || p.step == null) {
    return run.eta_seconds != null ? (
      <span className="text-xs text-muted-foreground">{t('queue.estimate', { time: duration(run.eta_seconds) })}</span>
    ) : null;
  }
  const share = p.total_steps > 0 ? (100 * p.step) / p.total_steps : 0;
  return (
    <div className={compact ? 'min-w-40 space-y-1' : 'space-y-2'}>
      <TkProgress value={share} aria-label={t('queue.progressLabel', { step: p.step, total: p.total_steps })} />
      <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
        <span>{t('queue.steps', { step: p.step, total: p.total_steps })}</span>
        {p.eta_seconds != null && run.status === 'running' && <span>{t('queue.left', { time: duration(p.eta_seconds) })}</span>}
        {!compact && p.loss != null && <span>{t('queue.loss', { loss: p.loss.toPrecision(3) })}</span>}
        {!compact && p.eval_loss != null && (
          <span>{t('queue.evalLoss', { loss: p.eval_loss.toPrecision(3), step: p.eval_step })}</span>
        )}
        {!compact && p.seconds_per_step != null && (
          <span>{t('queue.perStep', { seconds: p.seconds_per_step.toFixed(2) })}</span>
        )}
      </div>
    </div>
  );
}
