/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ImagesPage from '../ImagesPage';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const POLL_INTERVAL_MS = 3000;

const nginx = {
  id: 'img-1',
  name: 'nginx',
  registry: 'registry.example.com',
  repository: 'library/nginx',
  tag: '1.27',
  source_url: 'docker.io/library/nginx:1.27',
  destination_url: 'registry.example.com/library/nginx:1.27',
  description: 'Web server',
  category: 'user',
  protected: false,
  is_base: false,
  template: null,
  mirror_date: '2026-01-01T09:00:00+00:00',
  last_synced: null,
  metadata: {},
  digest: null,
  size_bytes: null,
  vulnerabilities: {},
};

const redis = {
  ...nginx,
  id: 'img-2',
  name: 'redis',
  repository: 'library/redis',
  tag: '7',
  source_url: 'docker.io/library/redis:7',
  destination_url: 'registry.example.com/library/redis:7',
  description: 'Cache',
};

const job = {
  id: 'job-1',
  image_id: 'img-2',
  job_type: 'mirror',
  status: 'running',
  source_url: 'docker.io/library/redis:7',
  error_message: null,
  logs: null,
  workflow: 'mirror-redis-abc',
};

const customImage = {
  id: 'c1',
  name: 'my-app',
  dockerfile_path: '~/dockerfiles/custom/my-app/Dockerfile',
  status: 'not_built',
  build_config: { description: 'My application' },
  output: null,
  workflow: null,
  registry_url: null,
  is_base: false,
  scope: 'general',
  parent_image_id: null,
  template: null,
  created_at: '2026-01-01T09:00:00+00:00',
};

// What GET /jobs/job-1 and GET /custom-images/c1/log answer, one entry for each reading
let jobAnswers: Record<string, unknown>[] = [];
let buildLogAnswers: Record<string, unknown>[] = [];

const reads = (url: string) => mockedApi.get.mock.calls.filter((call) => call[0] === url).length;

/** Draws the page, opens the dialog, names redis:7 and asks for the mirror. */
async function startMirror() {
  const page = render(<ImagesPage />);
  await screen.findByText('library/nginx');

  fireEvent.click(screen.getByRole('button', { name: 'Add image' }));
  fireEvent.change(await screen.findByLabelText('Source image'), {
    target: { value: 'docker.io/library/redis:7' },
  });
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Cache' } });
  fireEvent.click(screen.getByRole('button', { name: 'Mirror image' }));
  return page;
}

beforeEach(() => {
  vi.clearAllMocks();
  jobAnswers = [];
  buildLogAnswers = [];
  mockedApi.get.mockImplementation((url: string) => {
    if (url === '/images') return Promise.resolve({ data: { total: 1, skip: 0, limit: 50, images: [nginx] } });
    if (url === '/images/stats') {
      return Promise.resolve({ data: { total: 1, by_category: { system: 0, user: 1 }, protected: 0 } });
    }
    if (url === '/jobs/job-1') return Promise.resolve({ data: jobAnswers.shift() });
    if (url === '/custom-images') return Promise.resolve({ data: { builds: [customImage], total: 1 } });
    if (url === '/custom-images/c1/log') return Promise.resolve({ data: buildLogAnswers.shift() });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedApi.post.mockImplementation((url: string) => {
    if (url === '/images') return Promise.resolve({ data: { image: redis, job } });
    if (url === '/custom-images/c1/build') {
      return Promise.resolve({
        data: { build_id: 'c1', status: 'building', message: 'Build started', workflow: 'build-my-app-abc' },
      });
    }
    return Promise.reject(new Error(`unexpected POST ${url}`));
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ImagesPage', () => {
  it('lists the images returned by GET /images', async () => {
    render(<ImagesPage />);

    expect(await screen.findByText('library/nginx')).toBeInTheDocument();
    expect(screen.getByText('1.27')).toBeInTheDocument();
    expect(screen.getByText('Web server')).toBeInTheDocument();
    expect(mockedApi.get).toHaveBeenCalledWith('/images', { params: { skip: 0, limit: 50 } });
    expect(reads('/images/stats')).toBe(1);
  });

  it('mirrors a new image and reads its job until it succeeds', async () => {
    // The clock is a fake one that also follows real time, so that the waits of testing-library still end
    vi.useFakeTimers({ shouldAdvanceTime: true });
    jobAnswers = [
      { ...job, logs: 'copying layers' },
      { ...job, status: 'success', logs: 'copying layers\nall architectures copied' },
    ];

    await startMirror();

    await waitFor(() =>
      expect(mockedApi.post).toHaveBeenCalledWith('/images', {
        source_url: 'docker.io/library/redis:7',
        description: 'Cache',
      }),
    );
    expect(await screen.findByText('Mirroring redis:7')).toBeInTheDocument();
    expect(await screen.findByText(/copying layers/)).toBeInTheDocument();
    expect(screen.getByText('Running')).toBeInTheDocument();
    expect(reads('/jobs/job-1')).toBe(1);
    const listReadsWhileRunning = reads('/images');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });

    expect(await screen.findByText(/redis:7 is in Harbor now/)).toBeInTheDocument();
    expect(screen.getByText(/all architectures copied/)).toBeInTheDocument();
    expect(reads('/jobs/job-1')).toBe(2);
    await waitFor(() => expect(reads('/images')).toBeGreaterThan(listReadsWhileRunning));

    // A job that has ended is not read again
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * POLL_INTERVAL_MS);
    });
    expect(reads('/jobs/job-1')).toBe(2);
  });

  it('shows the error message of a mirror job that failed', async () => {
    jobAnswers = [
      {
        ...job,
        status: 'failed',
        logs: 'pulling docker.io/library/redis:7',
        error_message: 'manifest unknown: the source has no such tag',
      },
    ];

    await startMirror();

    expect(await screen.findByText(/manifest unknown: the source has no such tag/)).toBeInTheDocument();
    expect(screen.getByText(/pulling docker.io\/library\/redis:7/)).toBeInTheDocument();
    expect(reads('/jobs/job-1')).toBe(1);
  });

  it('stops reading a job when the page goes away', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    jobAnswers = [
      { ...job, logs: 'copying layers' },
      { ...job, logs: 'copying layers' },
    ];

    const { unmount } = await startMirror();
    expect(await screen.findByText(/copying layers/)).toBeInTheDocument();
    expect(reads('/jobs/job-1')).toBe(1);

    unmount();
    await vi.advanceTimersByTimeAsync(3 * POLL_INTERVAL_MS);

    expect(reads('/jobs/job-1')).toBe(1);
  });

  it('builds a custom image and reads its log until the build succeeds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    buildLogAnswers = [
      { status: 'building', workflow: 'build-my-app-abc', log: 'STEP 1/3: FROM library/python:3.12-slim' },
      { status: 'success', workflow: 'build-my-app-abc', log: 'STEP 3/3: CMD\nimage pushed' },
    ];

    render(<ImagesPage />);
    await screen.findByText('library/nginx');

    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Custom images' }));
    expect(await screen.findByText('my-app')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Build image' }));

    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledWith('/custom-images/c1/build', {}));
    expect(await screen.findByText('Building my-app')).toBeInTheDocument();
    expect(await screen.findByText(/STEP 1\/3/)).toBeInTheDocument();
    expect(reads('/custom-images/c1/log')).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });

    expect(await screen.findByText(/my-app was built and pushed to Harbor/)).toBeInTheDocument();
    expect(screen.getByText(/image pushed/)).toBeInTheDocument();
    expect(reads('/custom-images/c1/log')).toBe(2);
  });
});
