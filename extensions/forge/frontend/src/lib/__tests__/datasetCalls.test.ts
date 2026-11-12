/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '@/lib/axios';
import { forge } from '../forge';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.get.mockResolvedValue({ data: [] });
  mockedApi.post.mockResolvedValue({ data: {} });
});

describe('the calls about a dataset name its version', () => {
  it('lists the versions a run can train on', async () => {
    await forge.datasetVersions('d1');
    expect(mockedApi.get).toHaveBeenCalledWith('/datasets/d1/versions');
  });

  it('previews a split of one version', async () => {
    await forge.preview('d1', 1, 'org/base', 'train');
    expect(mockedApi.get).toHaveBeenCalledWith('/datasets/d1/preview', {
      params: { model: 'org/base', version: 1, split: 'train' },
    });
  });

  it('creates a run on one version', async () => {
    const run = {
      name: 'greeter',
      dataset_id: 'd1',
      dataset_version: 1,
      train_split: 'train',
      eval_split: null,
      eval_holdout: 0,
      base_model: 'org/base',
      method: 'lora' as const,
      node: 'tkspark',
      register: true,
      decisions: {},
      when: 'now' as const,
      across_slots: false,
      priority: 3,
      queue: false,
    };
    await forge.createRun(run as Parameters<typeof forge.createRun>[0]);
    expect(mockedApi.post.mock.calls[0][1]).toMatchObject({ dataset_id: 'd1', dataset_version: 1 });
  });
});
