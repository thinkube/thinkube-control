/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import LLMGatewayPage from '../LLMGatewayPage';
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

const qwen = {
  id: 'Qwen/Qwen3-8B-FP8',
  name: 'Qwen3 8B',
  server_type: ['vllm'],
  quantization: 'FP8',
  size: '9 GB',
  description: 'A small general model',
  state: 'available',
  backend_id: 'vllm-tkspark/Qwen/Qwen3-8B-FP8',
  tier: 'performance',
  is_finetuned: false,
  last_error: null,
  params_b: 8,
  active_params_b: null,
  context_length: 32768,
  reasoning_format: null,
  tool_use: true,
  stop_tokens: [],
  license: 'apache-2.0',
  gated: false,
  speculative_config: null,
};

const gptOss = {
  ...qwen,
  id: 'openai/gpt-oss-20b',
  name: 'gpt-oss 20B',
  description: 'A mixture of experts',
  state: 'deployable',
  backend_id: null,
  params_b: 21,
  active_params_b: 3.6,
  context_length: 131072,
};

const node = {
  name: 'tkspark',
  gpu_product: 'NVIDIA-GB10',
  gpu_family: 'blackwell',
  gpu_count: 1,
  gpu_replicas: 2,
  total_slots: 2,
  available_slots: 1,
  total_memory_gb: 119,
  per_gpu_memory_gb: 119,
  used_memory_gb: 9,
  shared_memory: true,
  is_uma: true,
  per_gpu_metrics: [],
  allocations: [
    {
      model_id: qwen.id,
      backend_id: qwen.backend_id,
      node_name: 'tkspark',
      estimated_memory_gb: 9,
      slots: 1,
    },
  ],
};

const backend = {
  id: 'vllm-tkspark/Qwen/Qwen3-8B-FP8',
  name: 'vLLM on tkspark',
  url: 'http://vllm-tkspark.vllm.svc:8000',
  type: 'vllm',
  status: 'healthy',
  models: [qwen.id],
};

// What GET /llm/models/ answers at the time it is read
let models: Record<string, unknown>[] = [];

/** The row of a table that names a model. */
const row = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  models = [qwen, gptOss];
  mockedApi.get.mockImplementation((url: string) => {
    if (url === '/llm/models/') return Promise.resolve({ data: { models } });
    if (url === '/llm/backends/') return Promise.resolve({ data: { backends: [backend], total: 1, healthy: 1 } });
    if (url === '/llm/gpu/status/') {
      return Promise.resolve({
        data: {
          nodes: [node],
          total_memory_gb: 119,
          used_memory_gb: 9,
          memory_threshold: 0.9,
          can_accept_new_model: true,
        },
      });
    }
    if (url === '/llm/backends/types') {
      return Promise.resolve({
        data: {
          backend_types: [
            { type: 'vllm', display_name: 'vLLM', namespace: 'vllm', driver: 'per-model-pod', tier: 'performance' },
          ],
        },
      });
    }
    if (url === `/llm/models/${gptOss.id}/load-options`) {
      return Promise.resolve({
        data: {
          model_id: gptOss.id,
          compatible_backends: [],
          gpu_nodes: [node],
          estimated_memory_gb: 14,
          context_length: gptOss.context_length,
        },
      });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
  mockedApi.post.mockImplementation((url: string) => {
    if (url === `/llm/models/${gptOss.id}/load`) {
      models = [qwen, { ...gptOss, state: 'loading', backend_id: 'vllm-tkspark/openai/gpt-oss-20b' }];
      return Promise.resolve({
        data: {
          model_id: gptOss.id,
          state: 'loading',
          message: 'Loading',
          backend_id: 'vllm-tkspark/openai/gpt-oss-20b',
        },
      });
    }
    if (url === `/llm/models/${qwen.id}/unload`) {
      models = [{ ...qwen, state: 'unloading' }, gptOss];
      return Promise.resolve({
        data: { model_id: qwen.id, state: 'unloading', message: 'Unloading', backend_id: qwen.backend_id },
      });
    }
    return Promise.reject(new Error(`unexpected POST ${url}`));
  });
});

describe('LLMGatewayPage', () => {
  it('lists the loadable models with their state', async () => {
    render(<LLMGatewayPage />);

    expect(await screen.findByText('Qwen3 8B')).toBeInTheDocument();
    expect(screen.getByText('A small general model')).toBeInTheDocument();
    expect(screen.getByText('21B / 3.6B active')).toBeInTheDocument();

    const loaded = within(row('Qwen3 8B'));
    expect(loaded.getByText('Available')).toBeInTheDocument();
    expect(loaded.getByText('vllm-tkspark')).toBeInTheDocument();
    expect(loaded.getByRole('button', { name: 'Unload' })).toBeInTheDocument();

    const deployable = within(row('gpt-oss 20B'));
    expect(deployable.getByText('Deployable')).toBeInTheDocument();
    expect(deployable.getByRole('button', { name: 'Load' })).toBeInTheDocument();
  });

  it('shows a GPU node with its memory, slots and allocations', async () => {
    render(<LLMGatewayPage />);

    expect(await screen.findByText('tkspark')).toBeInTheDocument();
    expect(screen.getByText('GB10')).toBeInTheDocument();
    expect(screen.getByText('blackwell')).toBeInTheDocument();
    expect(screen.getByText('Time-sliced')).toBeInTheDocument();
    expect(screen.getByText('Shared Memory')).toBeInTheDocument();
    expect(screen.getByText('Slots: 1 / 2 available')).toBeInTheDocument();
    expect(screen.getByText('Qwen3-8B-FP8 (9.0 GB)')).toBeInTheDocument();
  });

  it('loads a model through the dialog with POST /llm/models/{id}/load', async () => {
    render(<LLMGatewayPage />);
    await screen.findByText('gpt-oss 20B');

    fireEvent.click(within(row('gpt-oss 20B')).getByRole('button', { name: 'Load' }));

    expect(await screen.findByText('Estimated memory: 14.0 GB')).toBeInTheDocument();
    expect(mockedApi.get).toHaveBeenCalledWith(`/llm/models/${gptOss.id}/load-options`);

    const loadButton = screen.getByRole('button', { name: 'Load Model' });
    await waitFor(() => expect(loadButton).toBeEnabled());
    fireEvent.click(loadButton);

    await waitFor(() =>
      expect(mockedApi.post).toHaveBeenCalledWith(
        `/llm/models/${gptOss.id}/load`,
        expect.objectContaining({ backend: 'vllm', node: 'tkspark', max_context_length: 8192 }),
      ),
    );
    await waitFor(() => expect(screen.queryByText('Estimated memory: 14.0 GB')).not.toBeInTheDocument());
    expect(await within(row('gpt-oss 20B')).findByText('Loading...')).toBeInTheDocument();
  });

  it('unloads a model with POST /llm/models/{id}/unload', async () => {
    render(<LLMGatewayPage />);
    await screen.findByText('Qwen3 8B');

    fireEvent.click(within(row('Qwen3 8B')).getByRole('button', { name: 'Unload' }));

    await waitFor(() => expect(mockedApi.post).toHaveBeenCalledWith(`/llm/models/${qwen.id}/unload`, {}));
    expect(await within(row('Qwen3 8B')).findByText('Unloading...')).toBeInTheDocument();
  });

  it("shows a failed load's last_error", async () => {
    models = [qwen, { ...gptOss, last_error: 'The model does not fit the memory left on tkspark' }];

    render(<LLMGatewayPage />);
    await screen.findByText('gpt-oss 20B');

    const failed = within(row('gpt-oss 20B'));
    expect(failed.getByText('The model does not fit the memory left on tkspark')).toBeInTheDocument();
    expect(failed.getByRole('button', { name: 'Load' })).toBeInTheDocument();
  });

  it("shows the API's detail when a read fails", async () => {
    mockedApi.get.mockRejectedValue({ response: { data: { detail: 'Gateway registry is not ready' } } });

    render(<LLMGatewayPage />);

    expect(await screen.findByText('Gateway registry is not ready')).toBeInTheDocument();
  });
});
