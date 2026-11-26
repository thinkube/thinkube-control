/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { LossChart, smooth } from '../LossChart';

const POINTS = [{ step: 1, value: 2.5 }, { step: 2, value: 1.8 }, { step: 3, value: 1.2 }];

describe('LossChart', () => {
  it('says the loss comes with the first step while there is none', () => {
    render(<LossChart points={[]} />);
    expect(screen.getByText(/first step/)).toBeInTheDocument();
  });

  it('draws the raw loss faint behind its smoothed line', () => {
    const { container } = render(<LossChart points={POINTS} />);
    expect(screen.getByRole('img', { name: 'Training loss over 3 steps' })).toBeInTheDocument();
    const raw = container.querySelector('path[data-series="loss"]');
    expect(raw?.getAttribute('d')).toMatch(/^M.*L.*L/);
    expect(raw?.getAttribute('stroke-opacity')).toBe('0.3');
    expect(container.querySelector('path[data-series="smoothed"]')).not.toBeNull();
    expect(screen.getByText('Training loss, smoothed')).toBeInTheDocument();
  });

  it('draws the raw loss alone when the smoothing is none', () => {
    const { container } = render(<LossChart points={POINTS} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '0' } });
    expect(container.querySelector('path[data-series="smoothed"]')).toBeNull();
    expect(container.querySelector('path[data-series="loss"]')?.getAttribute('stroke-opacity')).toBe('1');
  });

  it('smooths as an exponential moving average from the first value', () => {
    expect(smooth([{ step: 1, value: 2 }, { step: 2, value: 1 }, { step: 3, value: 1 }], 0.5).map((p) => p.value))
      .toEqual([2, 1.5, 1.25]);
  });

  it('draws a long run with about two points per pixel column', () => {
    const many = Array.from({ length: 20000 }, (_, i) => ({ step: i + 1, value: 1 + Math.sin(i) }));
    const { container } = render(<LossChart points={many} />);
    const segments = (container.querySelector('path[data-series="loss"]')?.getAttribute('d') ?? '').split(/[ML]/).length - 1;
    expect(segments).toBeGreaterThan(100);
    expect(segments).toBeLessThanOrEqual(2 * 640);
  });

  it('draws the evaluation loss in its own colour with its legend', () => {
    const { container } = render(<LossChart points={POINTS} evalPoints={[{ step: 3, value: 1.4 }]} />);
    expect(container.querySelector('path[data-series="eval"]')).not.toBeNull();
    expect(screen.getByText('Evaluation loss')).toBeInTheDocument();
  });

  it('lists the values in a table made when it is opened', () => {
    const { container } = render(<LossChart points={POINTS} />);
    expect(screen.queryByText('1.20')).toBeNull();
    const details = container.querySelector('details') as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event('toggle'));
    expect(screen.getByText('1.20')).toBeInTheDocument();
  });
});
