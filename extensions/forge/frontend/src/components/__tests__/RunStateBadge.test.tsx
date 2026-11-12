/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RunStateBadge } from '../RunStateBadge';

describe('RunStateBadge', () => {
  it.each([
    ['planning', 'Planning'],
    ['queued', 'Queued'],
    ['running', 'Training'],
    ['stopping', 'Stopping'],
    ['paused', 'Paused'],
    ['completed', 'Done'],
    ['failed', 'Failed'],
    ['cancelled', 'Cancelled'],
  ] as const)('names the queue state %s in words, not by colour alone', (state, label) => {
    render(<RunStateBadge state={state} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('says which run preempted a paused run', () => {
    render(<RunStateBadge state="paused" preemptedBy="urgent fix" />);
    expect(screen.getByText('Paused (preempted by urgent fix)')).toBeInTheDocument();
  });
});
