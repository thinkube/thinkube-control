/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PrioritiesPage from '../PrioritiesPage';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: { get: vi.fn(), put: vi.fn() },
}));

const mockedApi = vi.mocked(api);

const kinds = [
  { kind: 'notebook_server', label: 'Notebook server', priority: 1, default: 1, gives_up: 'higher work waits for it' },
  { kind: 'served_model', label: 'Served model', priority: 4, default: 4, gives_up: 'the LLM gateway unloads it' },
];

describe('PrioritiesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.get.mockResolvedValue({ data: kinds } as never);
  });

  it('shows each kind of work with its priority and how it gives its GPUs up', async () => {
    render(<PrioritiesPage />);
    const row = await screen.findByTestId('priority-served_model');
    expect(within(row).getByText('Served model')).toBeTruthy();
    expect(within(row).getByText('the LLM gateway unloads it')).toBeTruthy();
    expect((within(row).getByLabelText('Priority of Served model') as HTMLSelectElement).value).toBe('4');
  });

  it('sets a kind of work to another priority', async () => {
    mockedApi.put.mockResolvedValue({ data: [kinds[0], { ...kinds[1], priority: 3 }] } as never);
    render(<PrioritiesPage />);
    const select = await screen.findByLabelText('Priority of Served model');
    fireEvent.change(select, { target: { value: '3' } });
    await waitFor(() => expect(mockedApi.put).toHaveBeenCalledWith('/priorities', { served_model: 3 }));
    await waitFor(() => expect((screen.getByLabelText('Priority of Served model') as HTMLSelectElement).value).toBe('3'));
  });
});
