/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RunStatusBadge } from '../RunStatusBadge';

describe('RunStatusBadge', () => {
  it.each([
    ['running', 'Training'],
    ['completed', 'Done'],
    ['error', 'Failed'],
    ['cancelled', 'Cancelled'],
  ] as const)('names the state %s in words, not by colour alone', (status, label) => {
    render(<RunStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
