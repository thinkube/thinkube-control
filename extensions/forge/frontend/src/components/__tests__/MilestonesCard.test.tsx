/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MilestonesCard } from '../MilestonesCard';
import type { RunMilestones } from '@/lib/forge';

const SETTINGS: RunMilestones = {
  every: 0.02,
  metrics: ['chrf', 'bleu'],
  eval: { id: 'f1', name: 'flores-dev', version: 1, split: 'train', rows: 400, group_column: 'direction', source_column: 'source' },
};

describe('MilestonesCard', () => {
  it('says how often and on what, before the first milestone', () => {
    render(<MilestonesCard settings={SETTINGS} made={[]} next={233} />);
    expect(screen.getByText(/Every 2% of the training/)).toBeInTheDocument();
    expect(screen.getByText(/400 rows of flores-dev v1, split train, scored with chrf, bleu/)).toBeInTheDocument();
    expect(screen.getByText('No milestone yet.')).toBeInTheDocument();
    expect(screen.getByText('Next milestone at step 233.')).toBeInTheDocument();
  });

  it('lists each milestone with its share of the training and each group score', () => {
    render(
      <MilestonesCard
        settings={SETTINGS}
        made={[{ step: 233, planned_steps: 11650, folder: '/m/step-233', seconds: 412.4,
          scores: { 'spa->arn': { chrf: 61.234, bleu: 30.5 }, 'cat->arn': { chrf: 70.1, bleu: 41.02 } } }]}
        next={466}
      />,
    );
    expect(screen.getByText('spa->arn chrf')).toBeInTheDocument();
    expect(screen.getByText('61.23')).toBeInTheDocument();
    expect(screen.getByText('2%')).toBeInTheDocument();
    expect(screen.getByText('412')).toBeInTheDocument();
  });
});
