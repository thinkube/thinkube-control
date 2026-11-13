/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ModelsPage from '../ModelsPage';
import api from '@/lib/axios';
import { useModelsStore, initialModelsState, POLL_INTERVAL_MS } from '@/stores/useModelsStore';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const qwen = {
  id: 'Qwen/Qwen3-8B-FP8',
  name: 'Qwen3 8B',
  size: '9 GB',
  quantization: 'FP8',
  description: 'A small general model',
  server_type: ['vllm'],
  is_downloaded: true,
  is_finetuned: false,
  task: 'text-generation',
  params_b: 8,
  active_params_b: null,
  context_length: 32768,
  reasoning_format: null,
  tool_use: true,
  license: 'apache-2.0',
  gated: false,
};

const gptOss = {
  ...qwen,
  id: 'openai/gpt-oss-20b',
  name: 'gpt-oss 20B',
  description: 'A mixture of experts',
  is_downloaded: false,
  params_b: 21,
  active_params_b: 3.6,
};

const job = {
  id: 'job-1',
  model_id: qwen.id,
  status: 'succeeded',
  workflow_name: 'model-mirror-qwen',
  workflow_url: 'https://argo.example.com/workflows/argo/model-mirror-qwen',
  error_message: null,
  created_at: '2026-01-01T09:00:00+00:00',
  updated_at: '2026-01-01T09:30:00+00:00',
  is_running: false,
  is_complete: true,
  is_failed: false,
};

const runningJob = {
  ...job,
  id: 'job-2',
  model_id: gptOss.id,
  status: 'running',
  workflow_name: 'model-mirror-gpt-oss',
  workflow_url: 'https://argo.example.com/workflows/argo/model-mirror-gpt-oss',
  is_running: true,
  is_complete: false,
};

// What GET /models/mirrors answers at the time it is read
let jobs: Record<string, unknown>[] = [];

const reads = (url: string) => mockedApi.get.mock.calls.filter((call) => call[0] === url).length;

/** The row of the table that names a model. */
const row = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  useModelsStore.getState().stopPolling();
  useModelsStore.setState(initialModelsState);
  jobs = [job];
  mockedApi.get.mockImplementation((url: string) => {
    if (url === '/models/catalog') return Promise.resolve({ data: { models: [qwen, gptOss] } });
    if (url === '/models/mirrors') return Promise.resolve({ data: { jobs } });
    if (url === '/models/mlflow/status') {
      return Promise.resolve({
        data: { initialized: true, needs_browser_login: false, mlflow_url: 'https://experiments.example.com' },
      });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedApi.post.mockImplementation((url: string) => {
    if (url === '/models/mirrors') {
      jobs = [job, runningJob];
      return Promise.resolve({
        data: {
          job_id: 'job-2',
          workflow_id: 'model-mirror-gpt-oss',
          model_id: gptOss.id,
          status: 'running',
          message: 'Mirror job submitted successfully',
        },
      });
    }
    return Promise.reject(new Error(`unexpected POST ${url}`));
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ModelsPage', () => {
  it('lists the models returned by GET /models/catalog', async () => {
    render(<ModelsPage />);

    expect(await screen.findByText('Qwen3 8B')).toBeInTheDocument();
    expect(screen.getByText('A small general model')).toBeInTheDocument();
    expect(screen.getByText('gpt-oss 20B')).toBeInTheDocument();
    expect(screen.getByText('21B / 3.6B active')).toBeInTheDocument();
    expect(reads('/models/catalog')).toBe(1);
    expect(reads('/models/mirrors')).toBe(1);
    expect(reads('/models/mlflow/status')).toBe(1);
  });

  it('marks a mirrored model as mirrored and ready', async () => {
    render(<ModelsPage />);
    await screen.findByText('Qwen3 8B');

    const mirrored = within(row('Qwen3 8B'));
    expect(await mirrored.findByText('Mirrored')).toBeInTheDocument();
    expect(mirrored.getByText('Ready')).toBeInTheDocument();
    expect(mirrored.queryByRole('button', { name: 'Mirror' })).not.toBeInTheDocument();

    expect(within(row('gpt-oss 20B')).getByText('Not Mirrored')).toBeInTheDocument();
  });

  it('starts a mirror with POST /models/mirrors and shows it running', async () => {
    render(<ModelsPage />);
    await screen.findByText('gpt-oss 20B');

    const mirrorButton = within(row('gpt-oss 20B')).getByRole('button', { name: 'Mirror' });
    // Mirroring waits for the Experiments status to say it is initialized
    await waitFor(() => expect(mirrorButton).toBeEnabled());
    fireEvent.click(mirrorButton);

    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledWith('/models/mirrors', { model_id: gptOss.id }));
    expect(await within(row('gpt-oss 20B')).findByText('Mirroring')).toBeInTheDocument();
    expect(screen.getByText('1 mirror operation in progress')).toBeInTheDocument();
    expect(within(row('gpt-oss 20B')).getByRole('link', { name: 'Monitor' })).toHaveAttribute(
      'href',
      'https://argo.example.com/workflows/argo/model-mirror-gpt-oss',
    );
  });

  it("shows a failed job's error", async () => {
    jobs = [
      job,
      {
        ...runningJob,
        status: 'failed',
        is_running: false,
        is_failed: true,
        error_message: 'MLflow does not know the user yet',
      },
    ];

    render(<ModelsPage />);
    await screen.findByText('gpt-oss 20B');

    const failed = within(row('gpt-oss 20B'));
    expect(await failed.findByText('Failed')).toBeInTheDocument();
    expect(failed.getByText('MLflow does not know the user yet')).toBeInTheDocument();
    expect(failed.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('reads the mirror jobs again while one runs, and stops when the page goes away', async () => {
    // The clock is a fake one that also follows real time, so that the waits of testing-library still end
    vi.useFakeTimers({ shouldAdvanceTime: true });
    jobs = [job, runningJob];

    const { unmount } = render(<ModelsPage />);
    await screen.findByText('gpt-oss 20B');
    expect(await within(row('gpt-oss 20B')).findByText('Mirroring')).toBeInTheDocument();
    expect(reads('/models/mirrors')).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(reads('/models/mirrors')).toBe(2);

    unmount();
    await vi.advanceTimersByTimeAsync(3 * POLL_INTERVAL_MS);

    expect(reads('/models/mirrors')).toBe(2);
  });
});
