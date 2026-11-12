/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DatasetPage from '../DatasetPage';
import type { AxiosRequestConfig } from 'axios';
import api from '@/lib/axios';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    put: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const split = (rows: number) => [{ name: 'train', rows, bytes: 100 }];
const columns = [{ name: 'text', type: 'VARCHAR' }];
const mapping = { kind: 'text', columns: { text: 'text' } };

const version = (n: number, extra: object) => ({
  dataset_id: 'd1',
  version: n,
  status: 'ready',
  error: null,
  splits: split(n === 1 ? 10 : 8),
  columns,
  mapping,
  rows: n === 1 ? 10 : 8,
  made_from: null,
  made_by: 'import',
  recipe: null,
  created_by: 'thinkube',
  created_at: '2026-01-01T09:00:00Z',
  ...extra,
});

const versions = [
  version(2, { made_from: 1, made_by: 'recipe', recipe: { id: 'r1', name: 'clean', document: {} } }),
  version(1, {}),
];

const dataset = (n: number) => ({
  id: 'd1',
  name: 'guanaco',
  source: 'huggingface',
  origin: 'someone/guanaco:default',
  created_by: 'thinkube',
  created_at: '2026-01-01T09:00:00Z',
  versions: 2,
  latest: 2,
  version: n,
  status: 'ready',
  error: null,
  splits: split(n === 1 ? 10 : 8),
  columns,
  mapping,
  mlflow_run_id: `run-${n}`,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.get.mockImplementation((url: string, config?: AxiosRequestConfig) => {
    const asked: number | undefined = config?.params?.version;
    if (url === '/datasets/d1/versions') return Promise.resolve({ data: versions });
    // The dataset without a version given is its latest ready one, v2
    if (url === '/datasets/d1') return Promise.resolve({ data: dataset(asked === undefined ? 2 : asked) });
    const shown = asked;
    return Promise.resolve({ data: { version: shown, split: 'train', offset: 0, rows: [], examples: [] } });
  });
});

const show = () =>
  render(
    <MemoryRouter initialEntries={['/d/d1']}>
      <Routes>
        <Route path="/d/:id" element={<DatasetPage />} />
      </Routes>
    </MemoryRouter>,
  );

describe('DatasetPage', () => {
  it('lists the versions with their lineage and row counts', async () => {
    show();
    const recipeRow = (await screen.findByText('Recipe clean, from v1')).closest('tr')!;
    expect(within(recipeRow).getByText('v2')).toBeInTheDocument();
    expect(within(recipeRow).getByText('8')).toBeInTheDocument();
    expect(within(recipeRow).getByText('Shown')).toBeInTheDocument();
    const importRow = screen.getByText('Import from Hugging Face').closest('tr')!;
    expect(within(importRow).getByText('10')).toBeInTheDocument();
  });

  it('reads the rows of the version chosen', async () => {
    show();
    const importRow = (await screen.findByText('Import from Hugging Face')).closest('tr')!;
    await userEvent.click(within(importRow).getByRole('button', { name: 'Show' }));
    await waitFor(() =>
      expect(mockedApi.get).toHaveBeenCalledWith('/datasets/d1/rows', {
        params: { version: 1, split: 'train', offset: 0, limit: 10 },
      }),
    );
    expect(await screen.findByText(/version 1/)).toBeInTheDocument();
  });
});
