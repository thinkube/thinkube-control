/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import JobsPage from '../JobsPage';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const base = {
  owner: 'forge', node: 'tkamd2', command: ['python', 'train.py'], memory_gb: 60, cpu_cores: 8, priority: 3,
  starts: 1, waiting: null, error: null, created_at: '2026-11-25T10:00:00+00:00', started_at: null, finished_at: null,
};
const jobs = [
  { ...base, id: 'j1', name: 'training of run 7', gpus: 2, gpu_memory_gb: null, status: 'running',
    gpu_ids: ['GPU-a', 'GPU-b'] },
  { ...base, id: 'j2', name: 'embeddings', owner: 'mill', gpus: 1, gpu_memory_gb: 4, status: 'waiting', gpu_ids: null,
    waiting: 'waiting for a GPU of tkamd2 (4.0 GiB on a GPU): GPU 0 is job training of run 7\'s alone' },
  { ...base, id: 'j3', name: 'clean', owner: 'mill', gpus: 0, gpu_memory_gb: null, status: 'failed', gpu_ids: null,
    error: "the job's process exited with code 1" },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe('JobsPage', () => {
  it('lists the jobs with what they ask for, where they are and what they wait for', async () => {
    mockedApi.get.mockResolvedValue({ data: { jobs, worker: { last_tick: null, error: null } } });
    render(
      <MemoryRouter>
        <JobsPage />
      </MemoryRouter>,
    );
    const running = within(await screen.findByTestId('job-j1'));
    expect(running.getByText('training of run 7')).toBeTruthy();
    expect(running.getByText('GPU-a, GPU-b')).toBeTruthy();
    expect(running.getByText(/2 GPUs, alone; 60 GiB, 8 cores/)).toBeTruthy();
    const waiting = within(screen.getByTestId('job-j2'));
    expect(waiting.getByText(/1 GPU, 4 GiB on each/)).toBeTruthy();
    expect(waiting.getByText(/waiting for a GPU of tkamd2/)).toBeTruthy();
    const failed = within(screen.getByTestId('job-j3'));
    expect(failed.getByText(/no GPU/)).toBeTruthy();
    expect(failed.getByText("the job's process exited with code 1")).toBeTruthy();
    expect(failed.queryByText('Stop')).toBeNull();
  });

  it('stops a job and says when the scheduler cannot follow the jobs', async () => {
    mockedApi.get.mockResolvedValue({ data: { jobs, worker: { last_tick: null, error: 'ApiException: 403' } } });
    mockedApi.post.mockResolvedValue({ data: {} });
    render(
      <MemoryRouter>
        <JobsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText('ApiException: 403')).toBeTruthy();
    fireEvent.click(within(screen.getByTestId('job-j1')).getByText('Stop'));
    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledWith('/jobs/j1/stop'));
  });
});
