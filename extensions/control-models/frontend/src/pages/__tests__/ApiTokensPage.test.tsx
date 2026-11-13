/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ApiTokensPage from '../ApiTokensPage';
import api from '@/lib/axios';
import { useTokensStore } from '@/stores/useTokensStore';

vi.mock('@/lib/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedApi = vi.mocked(api);

const token = {
  id: 'tok-1',
  name: 'CI pipeline',
  created_at: '2024-01-01T09:00:00',
  expires_at: null,
  last_used: null,
  is_active: true,
  scopes: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  useTokensStore.setState({ tokens: [], loading: false, error: null });
});

describe('ApiTokensPage', () => {
  it('lists the tokens returned by the API', async () => {
    mockedApi.get.mockResolvedValue({ data: [token] });

    render(<ApiTokensPage />);

    await waitFor(() => expect(mockedApi.get).toHaveBeenCalledWith('/tokens/'));
    expect(await screen.findByText('CI pipeline')).toBeInTheDocument();
    expect(screen.getAllByText('Never')).toHaveLength(2);
  });

  it('shows the empty state when no token exists', async () => {
    mockedApi.get.mockResolvedValue({ data: [] });

    render(<ApiTokensPage />);

    expect(await screen.findByText(/No API tokens yet/)).toBeInTheDocument();
  });

  it('keeps the create button disabled until a name is typed', async () => {
    const user = userEvent.setup();
    mockedApi.get.mockResolvedValue({ data: [] });

    render(<ApiTokensPage />);
    await screen.findByText(/No API tokens yet/);

    const createButton = screen.getByRole('button', { name: 'Create Token' });
    expect(createButton).toBeDisabled();

    await user.type(screen.getByLabelText('Token Name'), 'My token');
    expect(createButton).toBeEnabled();
  });

  it('creates a token and shows the secret once', async () => {
    const user = userEvent.setup();
    mockedApi.get.mockResolvedValue({ data: [] });
    mockedApi.post.mockResolvedValue({
      data: { id: 'tok-2', name: 'My token', token: 'tk_secret_value', scopes: [] },
    });

    render(<ApiTokensPage />);
    await screen.findByText(/No API tokens yet/);

    await user.type(screen.getByLabelText('Token Name'), 'My token');
    await user.type(screen.getByLabelText('Expires In (days)'), '30');
    await user.click(screen.getByRole('button', { name: 'Create Token' }));

    await waitFor(() =>
      expect(mockedApi.post).toHaveBeenCalledWith('/tokens/', {
        name: 'My token',
        expires_in_days: 30,
        scopes: [],
      }),
    );
    expect(await screen.findByText('tk_secret_value')).toBeInTheDocument();
  });

  it('revokes a token once the confirmation is accepted', async () => {
    const user = userEvent.setup();
    mockedApi.get.mockResolvedValue({ data: [token] });
    mockedApi.delete.mockResolvedValue({ data: {} });

    render(<ApiTokensPage />);
    await screen.findByText('CI pipeline');

    await user.click(within(screen.getByTestId('token-row')).getByRole('button', { name: 'Revoke' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Revoke' }));

    await waitFor(() => expect(mockedApi.delete).toHaveBeenCalledWith('/tokens/tok-1'));
  });

  it('disables the button for a revoked token', async () => {
    mockedApi.get.mockResolvedValue({ data: [{ ...token, is_active: false }] });

    render(<ApiTokensPage />);

    expect(await screen.findByRole('button', { name: 'Revoked' })).toBeDisabled();
  });
});
