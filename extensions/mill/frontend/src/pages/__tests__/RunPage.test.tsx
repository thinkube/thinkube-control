/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import RunPage from '../RunPage';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const ID = '20261116-160000-clean-ab12';

function detail(state: string, canStop: boolean) {
  return {
    run: {
      id: ID,
      task: 'clean',
      description: 'Keep the rows with enough words.',
      file: 'kubexlat/clean.py',
      repository: { root: '/storage/thinkube-shared/mill', commit: '31ce40c0ffee', dirty: true },
      inputs: { rows: { type: 'jsonl', path: 'data/rows.jsonl' } },
      outputs: { cleaned: { type: 'jsonl', path: 'data/cleaned.jsonl' } },
      settings: { min_words: 5 },
      limit: null,
      host: 'mill-backend-1',
      runner: 'mill',
    },
    status: {
      state,
      error: state === 'failed' ? 'ValueError: row 3 is broken' : null,
      started_at: '2026-11-16T16:00:00.000+00:00',
      updated_at: '2026-11-16T16:00:05.000+00:00',
      ended_at: state === 'running' ? null : '2026-11-16T16:01:05.000+00:00',
      stages: [{ name: 'cleaning', total: 50, done: 50, started_at: '2026-11-16T16:00:00.000+00:00', ended_at: null }],
      counters: { 'dropped: too short': 7 },
      measures: { words: { count: 50, sum: 600, min: 1, max: 40 } },
      samples: 43,
      failures: 1,
    },
    can_stop: canStop,
    has_log: true,
  };
}

function answers(state: string, canStop: boolean) {
  mockedApi.get.mockImplementation((url: string) => {
    if (url === `/runs/${ID}`) return Promise.resolve({ data: detail(state, canStop) });
    if (url === `/runs/${ID}/samples`) {
      return Promise.resolve({
        data: {
          latest: [{ n: 43, at: 'x', fields: { input: { text: ' raw text ' }, output: 'clean text' } }],
          spaced: [{ n: 1, at: 'x', fields: { input: { text: ' first ' }, output: 'first' } }],
        },
      });
    }
    if (url === `/runs/${ID}/failures`) {
      return Promise.resolve({ data: { failures: [{ n: 1, at: 'x', error: 'no text', fields: { input: { id: 9 } } }], total: 1 } });
    }
    if (url === `/runs/${ID}/log`) return Promise.resolve({ data: { lines: ['cleaning starts'], total_lines: 1 } });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

function open() {
  render(
    <MemoryRouter initialEntries={[`/runs/${ID}`]}>
      <Routes>
        <Route path="/runs/:id" element={<RunPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('RunPage', () => {
  it('shows the progress, totals, samples, failures and log of a run', async () => {
    answers('completed', false);
    open();

    expect(await screen.findByText('Keep the rows with enough words.')).toBeTruthy();
    expect(screen.getByText('completed')).toBeTruthy();
    expect(screen.getByText(/50 of 50/)).toBeTruthy();
    expect(screen.getByText('1m 05s', { exact: false })).toBeTruthy();
    expect(screen.getByText('31ce40c0ff')).toBeTruthy();
    expect(screen.getByText(/with changes not committed/)).toBeTruthy();
    expect(screen.getByText('dropped: too short')).toBeTruthy();
    expect(screen.getByText('12.000')).toBeTruthy();
    expect(await screen.findByText('clean text')).toBeTruthy();
    expect(await screen.findByText('no text')).toBeTruthy();
    expect(await screen.findByText('cleaning starts')).toBeTruthy();
    expect(screen.queryByText('Stop')).toBeNull();

    fireEvent.click(screen.getByText('Across the run'));
    expect(await screen.findByText('first')).toBeTruthy();
  });

  it('shows the error of a failed run', async () => {
    answers('failed', false);
    open();
    expect(await screen.findByText('ValueError: row 3 is broken')).toBeTruthy();
    expect(screen.getByText('The run failed')).toBeTruthy();
  });

  it('stops a run the control center executes', async () => {
    answers('running', true);
    mockedApi.post.mockResolvedValue({ data: { id: ID, asked: 'stop' } });
    open();

    fireEvent.click(await screen.findByText('Stop'));
    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledWith(`/runs/${ID}/stop`));
  });

  it('says why a run that does not exist could not be read', async () => {
    mockedApi.get.mockRejectedValue({ response: { data: { detail: `there is no run ${ID}` } } });
    open();
    expect(await screen.findByText(`there is no run ${ID}`)).toBeTruthy();
  });
});
