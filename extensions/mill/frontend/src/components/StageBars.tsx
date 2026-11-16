/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { duration, percent, secondsLeft, type Stage } from '@/lib/mill';

/** One progress bar for each stage of a run. */
export function StageBars({ stages, failed }: { stages: Stage[]; failed?: boolean }) {
  if (!stages.length) return <span className="text-xs text-muted-foreground">no stage reported</span>;
  const now = Date.now();
  return (
    <div className="space-y-2 min-w-[14rem]">
      {stages.map((stage) => {
        const pct = percent(stage);
        const left = secondsLeft(stage, now);
        const stopped = stage.ended_at !== null && stage.total !== null && stage.done < stage.total;
        const color = stopped || failed ? 'bg-amber-500' : 'bg-primary';
        return (
          <div key={stage.name}>
            <div className="flex justify-between gap-4 text-xs">
              <span className="font-medium">{stage.name}</span>
              <span className="text-muted-foreground">
                {stage.total === null ? `${stage.done} done` : `${stage.done} of ${stage.total}`}
                {left !== null && ` · ${duration(left)} left`}
              </span>
            </div>
            <div
              className="h-2 w-full rounded bg-muted overflow-hidden"
              role="progressbar"
              aria-label={stage.name}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct === null ? undefined : Math.round(pct)}
            >
              <div
                className={`h-full ${color} ${pct === null && stage.ended_at === null ? 'animate-pulse' : ''}`}
                style={{ width: `${pct === null ? 100 : pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
