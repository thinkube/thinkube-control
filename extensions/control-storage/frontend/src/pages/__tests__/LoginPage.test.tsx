/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import LoginPage from '../LoginPage';
import { redirectToLogin, isAuthenticated } from '@/lib/auth';

vi.mock('@/lib/auth', () => ({
  redirectToLogin: vi.fn(),
  isAuthenticated: vi.fn(),
}));

const mockedRedirect = vi.mocked(redirectToLogin);
const mockedIsAuthenticated = vi.mocked(isAuthenticated);

function renderPage(state?: { from?: string }) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state }]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>Tasks page</div>} />
        <Route path="/tokens" element={<div>Tokens page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedRedirect.mockResolvedValue(undefined);
});

describe('LoginPage', () => {
  it('sends an anonymous visitor to Keycloak', async () => {
    mockedIsAuthenticated.mockReturnValue(false);

    renderPage();

    expect(screen.getByRole('heading', { name: 'Redirecting to login...' })).toBeInTheDocument();
    await waitFor(() => expect(mockedRedirect).toHaveBeenCalledWith(undefined));
  });

  it('passes the intended route along to Keycloak', async () => {
    mockedIsAuthenticated.mockReturnValue(false);

    renderPage({ from: '/tokens' });

    await waitFor(() => expect(mockedRedirect).toHaveBeenCalledWith('/tokens'));
  });

  it('sends an already authenticated user straight to their route', () => {
    mockedIsAuthenticated.mockReturnValue(true);

    renderPage({ from: '/tokens' });

    expect(screen.getByText('Tokens page')).toBeInTheDocument();
    expect(mockedRedirect).not.toHaveBeenCalled();
  });
});
