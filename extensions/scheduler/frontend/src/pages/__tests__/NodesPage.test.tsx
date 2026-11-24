/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import NodesPage from '../NodesPage';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);
const GIB = 2 ** 30;

const nodes = [
  {
    name: 'tkamd2',
    kind: 'separate',
    memory_total_bytes: 125 * GIB,
    memory_available_bytes: 114 * GIB,
    gpus: [
      { id: 'GPU-a', index: 0, name: 'NVIDIA GeForce RTX 3090', utilization: 0, temperature: 54, power: 25,
        memory_total_bytes: 24 * GIB, memory_used_bytes: 1.5 * GIB, memory_free_bytes: 22 * GIB },
      { id: 'GPU-b', index: 1, name: 'NVIDIA GeForce RTX 3090', utilization: 97, temperature: 72, power: 279,
        memory_total_bytes: 24 * GIB, memory_used_bytes: 22 * GIB, memory_free_bytes: 2 * GIB },
    ],
  },
  {
    name: 'tkspark',
    kind: 'shared',
    memory_total_bytes: 122 * GIB,
    memory_available_bytes: 35 * GIB,
    gpus: [{ id: 'GPU-s', index: 0, name: 'NVIDIA GB10', utilization: 0, temperature: 63, power: 10 }],
  },
  { name: 'tkamd1', error: 'the node-metrics pod of tkamd1 did not answer' },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe('NodesPage', () => {
  it('lists each node with its kind and its GPUs by id', async () => {
    mockedApi.get.mockResolvedValue({ data: { nodes } });
    render(
      <MemoryRouter>
        <NodesPage />
      </MemoryRouter>,
    );

    const amd = within(await screen.findByTestId('node-tkamd2'));
    expect(amd.getByText(/Each GPU has memory of its own/)).toBeTruthy();
    expect(amd.getByText(/114\.0 GiB available of 125\.0 GiB/)).toBeTruthy();
    expect(amd.getByText('GPU-a')).toBeTruthy();
    expect(amd.getByText('GPU-b')).toBeTruthy();
    expect(amd.getByText('22.0 GiB of 24.0 GiB')).toBeTruthy();
    expect(amd.getByText('97%')).toBeTruthy();

    const spark = within(screen.getByTestId('node-tkspark'));
    expect(spark.getByText(/shares one memory/)).toBeTruthy();
    expect(spark.getByText("the node's memory")).toBeTruthy();

    expect(within(screen.getByTestId('node-tkamd1')).getByText(/did not answer/)).toBeTruthy();
  });

  it('says why the nodes could not be read', async () => {
    mockedApi.get.mockRejectedValue({ response: { data: { detail: 'no node-metrics pod is running' } } });
    render(
      <MemoryRouter>
        <NodesPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText('no node-metrics pod is running')).toBeTruthy();
  });
});
