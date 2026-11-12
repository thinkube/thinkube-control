/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import StoragePage from '../StoragePage';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const GIB = 1024 ** 3;

const shared = {
  filesystems: [
    {
      name: 'thinkube-shared',
      used_bytes: 200 * GIB,
      trash_days: 1,
      root_claims: [],
      folders: [
        {
          name: 'pvc-models',
          path: 'pvc-models',
          bytes: 200 * GIB,
          dirs: 3,
          files: 259,
          quota_bytes: 600 * GIB,
          claims: [],
          volumes: ['pvc-models'],
          quota_at_next_mount_bytes: 600 * GIB,
        },
      ],
    },
  ],
  disks: [{ server: 'volume-0', node: 'tkspark', dir: '/data', total_bytes: 1000 * GIB, used_bytes: 300 * GIB, free_bytes: 700 * GIB }],
};

const claims = {
  claims: [
    {
      namespace: 'gitea',
      name: 'gitea-data',
      storage_class: 'k8s-hostpath',
      volume: 'pvc-1',
      phase: 'Bound',
      requested_bytes: 10 * GIB,
      capacity_bytes: 10 * GIB,
      used_bytes: 2 * GIB,
    },
  ],
  errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.get.mockImplementation((url: string) => {
    if (url === '/storage') return Promise.resolve({ data: shared });
    if (url === '/storage/nodes') return Promise.resolve({ data: { nodes: [{ node: 'tkspark', error: 'no running node-metrics pod on tkspark' }] } });
    if (url === '/storage/claims') return Promise.resolve({ data: claims });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
});

describe('StoragePage', () => {
  it('reads the shared folders, the node disks and the volume claims', async () => {
    render(<StoragePage />);

    expect((await screen.findAllByText('pvc-models')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('gitea-data')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Shared folders').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Node disks').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Volume claims').length).toBeGreaterThan(0);
    const asked = mockedApi.get.mock.calls.map((call) => call[0]);
    expect(asked).toEqual(expect.arrayContaining(['/storage', '/storage/nodes', '/storage/claims']));
  });

  it('shows why a part could not be read', async () => {
    mockedApi.get.mockImplementation((url: string) =>
      url === '/storage'
        ? Promise.reject({ response: { data: { detail: 'secret juicefs/juicefs-secret of a JuiceFS file system: Forbidden' } } })
        : Promise.resolve({ data: url === '/storage/nodes' ? { nodes: [] } : { claims: [], errors: [] } }),
    );
    render(<StoragePage />);

    expect((await screen.findAllByText(/juicefs-secret of a JuiceFS file system: Forbidden/)).length).toBeGreaterThan(0);
  });
});
