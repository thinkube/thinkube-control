/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EpochScoresCard } from '../EpochScoresCard';
import { epochMetrics, taskOfKind } from '@/lib/forge';

const RECORDS = [
  { epoch: 1, step: 450, upos: 0.9612, feats: 0.9, lemma: 0.95, uas: 0.85, las: 0.8, words: 12000, score: 0.8778 },
  { epoch: 2, step: 900, upos: 0.9701, feats: 0.92, lemma: 0.96, uas: 0.88, las: 0.84, words: 12000, score: 0.8975 },
];

describe('EpochScoresCard', () => {
  it('lists each epoch with every task accuracy, UAS and LAS in percent, the best epoch marked', () => {
    const { container } = render(<EpochScoresCard records={RECORDS} epochs={20} />);
    expect(screen.getByText('UPOS')).toBeInTheDocument();
    expect(screen.getByText('LAS')).toBeInTheDocument();
    expect(screen.getByText('97.01')).toBeInTheDocument();
    expect(screen.getByText('2 / 20')).toBeInTheDocument();
    expect(container.querySelector('tr[data-best="true"]')?.textContent).toContain('97.01');
    expect(screen.getByText(/epoch 2/)).toBeInTheDocument();
  });

  it('says no epoch has ended before the first one', () => {
    render(<EpochScoresCard records={[]} epochs={20} />);
    expect(screen.getByText('No epoch has ended yet.')).toBeInTheDocument();
  });
});

describe('a tagging run', () => {
  it('orders the tasks before the parser', () => {
    expect(epochMetrics(RECORDS)).toEqual(['feats', 'lemma', 'upos', 'uas', 'las']);
  });

  it('is the task of a dataset of words', () => {
    expect(taskOfKind('words')).toBe('tagging');
    expect(taskOfKind('choice')).toBe('choice');
    expect(taskOfKind('prompt')).toBe('sft');
  });
});
