/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider } from '../ThemeProvider';
import { ThemeToggle } from '../ThemeToggle';

function renderToggle() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove('light', 'dark');
});

describe('ThemeToggle', () => {
  it('starts on the light theme when the system prefers light', () => {
    renderToggle();

    expect(document.documentElement).toHaveClass('light');
  });

  it('switches the document to dark and remembers the choice', async () => {
    const user = userEvent.setup();

    renderToggle();
    await user.click(screen.getByRole('button', { name: 'Toggle theme' }));

    expect(document.documentElement).toHaveClass('dark');
    expect(localStorage.getItem('theme')).toBe('dark');
  });

  it('switches back to light', async () => {
    const user = userEvent.setup();

    renderToggle();
    const button = screen.getByRole('button', { name: 'Toggle theme' });
    await user.click(button);
    await user.click(button);

    expect(document.documentElement).toHaveClass('light');
    expect(localStorage.getItem('theme')).toBe('light');
  });
});
