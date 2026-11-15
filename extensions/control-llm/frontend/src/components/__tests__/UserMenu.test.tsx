/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UserMenu } from '../UserMenu';
import { useAuthStore } from '@/stores/useAuthStore';

function renderMenu() {
  return render(
    <MemoryRouter>
      <UserMenu />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({
    user: { sub: 'u1', preferred_username: 'alice', email: 'alice@example.com' },
  });
});

describe('UserMenu', () => {
  it('renders nothing while no user is loaded', () => {
    useAuthStore.setState({ user: null });

    const { container } = renderMenu();

    expect(container).toBeEmptyDOMElement();
  });

  it('shows the username on the trigger', () => {
    renderMenu();

    expect(screen.getByRole('button', { name: /alice/ })).toBeInTheDocument();
  });

  it('falls back to the email when there is no username', () => {
    useAuthStore.setState({ user: { sub: 'u1', email: 'alice@example.com' } });

    renderMenu();

    expect(screen.getByRole('button', { name: /alice@example.com/ })).toBeInTheDocument();
  });
});
