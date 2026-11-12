/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect } from 'vitest';
import { cellText, decisionField, duration, failure, parseDecision } from '../forge';

describe('cellText', () => {
  it('joins what a cell printed and ends with its error', () => {
    expect(
      cellText({
        success: false,
        cell_index: 4,
        outputs: [
          { output_type: 'stream', name: 'stdout', text: 'step 1\n' },
          { output_type: 'error', ename: 'OutOfMemoryError', evalue: 'CUDA out of memory' },
        ],
      }),
    ).toBe('step 1\nOutOfMemoryError: CUDA out of memory');
  });
});

describe('duration', () => {
  it('reads seconds as minutes, then hours and minutes', () => {
    expect(duration(null)).toBe('–');
    expect(duration(600)).toBe('10 min');
    expect(duration(3 * 3600 + 5 * 60)).toBe('3 h 05 min');
  });
});

describe('parseDecision', () => {
  it('reads what a person types as the kind of value the plan has', () => {
    expect(parseDecision('32', 16)).toBe(32);
    expect(parseDecision('2e-4', 1e-4)).toBe(2e-4);
    expect(parseDecision('false', true)).toBe(false);
    expect(parseDecision('cosine', 'constant')).toBe('cosine');
  });
});

describe('decisionField', () => {
  it('sets the evaluation of a plan through eval_every, every other decision by its name', () => {
    expect(decisionField('evaluation')).toBe('eval_every');
    expect(decisionField('rank')).toBe('rank');
  });
});

describe('failure', () => {
  it("reads the API's detail from an error of another copy of axios, as Thinkube Control's", () => {
    const fromControl = Object.assign(new Error('Request failed with status code 409'), {
      isAxiosError: true,
      response: { status: 409, data: { detail: 'the NVFP4 export needs the Python environment nvfp4' } },
    });
    expect(failure(fromControl)).toBe('the NVFP4 export needs the Python environment nvfp4');
    expect(failure(new Error('plain'))).toBe('plain');
  });
});
