/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, afterEach } from 'vitest';
import { publicValue } from '../publicConfig';

afterEach(() => {
  delete window.__PUBLIC_CONFIG__;
});

describe('publicValue', () => {
  it('returns undefined when the deployment published nothing', () => {
    expect(publicValue('APP_TITLE')).toBeUndefined();
  });

  it('returns the value the deployment published', () => {
    window.__PUBLIC_CONFIG__ = { APP_TITLE: 'Acme Tasks' };

    expect(publicValue('APP_TITLE')).toBe('Acme Tasks');
  });

  it('returns undefined for a name that was not published', () => {
    window.__PUBLIC_CONFIG__ = { APP_TITLE: 'Acme Tasks' };

    expect(publicValue('POSTGRES_PASSWORD')).toBeUndefined();
  });

  it('treats an empty value as absent, so the caller falls back', () => {
    window.__PUBLIC_CONFIG__ = { APP_TITLE: '' };

    expect(publicValue('APP_TITLE')).toBeUndefined();
  });
});
