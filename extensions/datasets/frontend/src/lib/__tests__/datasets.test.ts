/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect } from 'vitest';
import { cleanMapping, complete, size } from '../datasets';

describe('mapping', () => {
  it('keeps only the parts of its kind that name a column', () => {
    expect(cleanMapping('prompt', { prompt: 'q', completion: 'a', system: '', text: 't' })).toEqual({
      kind: 'prompt',
      columns: { prompt: 'q', completion: 'a' },
    });
  });

  it('is complete when every required part names a column', () => {
    expect(complete({ kind: 'instruction', columns: { instruction: 'i' } })).toBe(false);
    expect(complete({ kind: 'instruction', columns: { instruction: 'i', output: 'o' } })).toBe(true);
  });
});

describe('size', () => {
  it('reads as the largest whole unit', () => {
    expect(size(2048)).toBe('2.0 KB');
    expect(size(3 * 1024 ** 3)).toBe('3.00 GB');
  });
});
