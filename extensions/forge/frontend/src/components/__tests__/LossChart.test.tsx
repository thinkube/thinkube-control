/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LossChart } from '../LossChart';

describe('LossChart', () => {
  it('says the loss comes with the first step while there is none', () => {
    render(<LossChart points={[]} />);
    expect(screen.getByText(/first step/)).toBeInTheDocument();
  });

  it('draws one line through the steps and lists their values', () => {
    const { container } = render(
      <LossChart points={[{ step: 1, value: 2.5 }, { step: 2, value: 1.8 }, { step: 3, value: 1.2 }]} />,
    );
    expect(screen.getByRole('img', { name: 'Training loss over 3 steps' })).toBeInTheDocument();
    expect(container.querySelector('path')?.getAttribute('d')).toMatch(/^M.*L.*L/);
    expect(screen.getByText('1.20')).toBeInTheDocument();
  });
});
