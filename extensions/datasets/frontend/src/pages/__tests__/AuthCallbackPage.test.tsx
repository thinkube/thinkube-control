/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import AuthCallbackPage from '../AuthCallbackPage';
import { handleAuthCallback } from '@/lib/auth';

vi.mock('@/lib/auth', () => ({
  handleAuthCallback: vi.fn(),
}));

const mockedHandleAuthCallback = vi.mocked(handleAuthCallback);

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/auth/callback${search}`]}>
      <Routes>
        <Route path="/auth/callback" element={<AuthCallbackPage />} />
        <Route path="/" element={<div>Tasks page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AuthCallbackPage', () => {
  it('exchanges the code and lands on the home route', async () => {
    mockedHandleAuthCallback.mockResolvedValue({ access_token: 'a', expires_in: 300 });

    renderAt('?code=abc123');

    await waitFor(() => expect(mockedHandleAuthCallback).toHaveBeenCalledWith('abc123'));
    expect(await screen.findByText('Tasks page')).toBeInTheDocument();
  });

  it('returns to the route the user was heading for', async () => {
    sessionStorage.setItem('intendedRoute', '/');
    mockedHandleAuthCallback.mockResolvedValue({ access_token: 'a', expires_in: 300 });

    renderAt('?code=abc123');

    expect(await screen.findByText('Tasks page')).toBeInTheDocument();
    expect(sessionStorage.getItem('intendedRoute')).toBeNull();
  });

  it('reports the error Keycloak sent back', async () => {
    renderAt('?error=access_denied');

    expect(await screen.findByText(/access_denied/)).toBeInTheDocument();
    expect(mockedHandleAuthCallback).not.toHaveBeenCalled();
  });

  it('reports a callback with no code', async () => {
    renderAt('');

    expect(await screen.findByText('No authorization code received')).toBeInTheDocument();
  });

  it('reports a failed exchange', async () => {
    mockedHandleAuthCallback.mockRejectedValue(new Error('boom'));

    renderAt('?code=abc123');

    expect(
      await screen.findByRole('heading', { name: 'Authentication failed' }),
    ).toBeInTheDocument();
  });
});
