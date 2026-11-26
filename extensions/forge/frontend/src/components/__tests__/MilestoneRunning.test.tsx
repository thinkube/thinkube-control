/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MilestoneRunning } from '../MilestoneRunning';
import { TrainingProgress } from '../TrainingProgress';
import { milestoneLeft, type Run } from '@/lib/forge';

const RUNNING = { step: 1000, answered: 2360, rows: 9438, started_at: '2026-11-26T12:15:00+00:00' };
// 20 minutes for 2,360 rows: the other 7,078 take 60 minutes more
const UPDATED = '2026-11-26T12:35:00+00:00';

describe('MilestoneRunning', () => {
  it('says how many rows of how many the milestone has answered, and the time it has left', () => {
    render(<MilestoneRunning running={RUNNING} updated={UPDATED} />);
    expect(screen.getByText('Milestone at step 1,000: 2,360 of 9,438 rows answered (25%)')).toBeInTheDocument();
    expect(screen.getByText(/1 h 00 min left/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Rows answered by the milestone at step 1000' })).toBeInTheDocument();
  });

  it('says its share alone in the list of runs', () => {
    render(<MilestoneRunning running={RUNNING} updated={UPDATED} compact />);
    expect(screen.getByText('milestone 25%')).toBeInTheDocument();
  });

  it('has no time left before its first row', () => {
    expect(milestoneLeft({ ...RUNNING, answered: 0 }, UPDATED)).toBeNull();
    expect(milestoneLeft(RUNNING, UPDATED)).toBeCloseTo(3598.8, 0);
  });

  it('is shown in the progress of a running training, and not once the run has stopped', () => {
    const progress = { state: 'training', step: 1000, total_steps: 26697, loss: 0.9, seconds_per_step: 4, eta_seconds: 1,
      checkpoint: null, plan: null, error: null, updated: UPDATED, milestone_running: RUNNING } as const;
    const run = { status: 'running', progress, eta_seconds: null } as unknown as Run;
    const { rerender } = render(<TrainingProgress run={run} />);
    expect(screen.getByText(/2,360 of 9,438 rows answered/)).toBeInTheDocument();
    rerender(<TrainingProgress run={{ ...run, status: 'paused' } as Run} />);
    expect(screen.queryByText(/rows answered/)).toBeNull();
  });
});
