/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import RunsPage from '../RunsPage';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const run = {
  id: '20261116-160000-clean-ab12',
  task: 'clean',
  file: 'kubexlat/clean.py',
  limit: 50,
  tags: { experiment: 'vote-of-finders', setup: 'plain' },
  host: 'mill-backend-1',
  node: null,
  runner: 'mill',
  commit: '31ce40c',
  dirty: false,
  state: 'running',
  error: null,
  started_at: '2026-11-16T16:00:00.000+00:00',
  updated_at: '2026-11-16T16:00:05.000+00:00',
  ended_at: null,
  stages: [{ name: 'cleaning', total: 50, done: 20, started_at: '2026-11-16T16:00:00.000+00:00', ended_at: null }],
  samples: 20,
  failures: 2,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('RunsPage', () => {
  it('lists the runs with their state and a bar for each stage', async () => {
    mockedApi.get.mockResolvedValue({ data: { runs: [run], total: 1 } });
    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('clean')).toBeTruthy();
    expect(screen.getByText('running')).toBeTruthy();
    expect(screen.getByText(/kubexlat\/clean\.py/)).toBeTruthy();
    expect(screen.getByText(/20 of 50/)).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: 'cleaning' }).getAttribute('aria-valuenow')).toBe('40');
    expect(screen.getByText('2 failed items')).toBeTruthy();
    expect(screen.getByText('experiment: vote-of-finders')).toBeTruthy();
    expect(screen.getByText('setup: plain')).toBeTruthy();
    expect(screen.getByText('mill-backend-1')).toBeTruthy();
    expect(mockedApi.get.mock.calls[0][0]).toBe('/runs');
  });

  it('says so when there is no run', async () => {
    mockedApi.get.mockResolvedValue({ data: { runs: [], total: 0 } });
    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/No run yet/)).toBeTruthy();
  });

  it('says so when the control center receives nothing from the runs', async () => {
    mockedApi.get.mockResolvedValue({
      data: { runs: [run], total: 1, problem: 'the control center cannot read the runs\' messages from NATS' },
    });
    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/cannot read the runs' messages from NATS/)).toBeTruthy();
    expect(screen.getByText('clean')).toBeTruthy();
  });

  it('shows why the runs could not be read', async () => {
    mockedApi.get.mockRejectedValue({ response: { data: { detail: 'the shared file system is not mounted' } } });
    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/the shared file system is not mounted/)).toBeTruthy();
  });
});
