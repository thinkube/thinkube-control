/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { describe, it, expect, vi } from 'vitest';
import type { AxiosRequestConfig, AxiosResponse } from 'axios';
import api, { hostRequests } from '../axios';

describe('hostRequests', () => {
  it('sends every call through the host, with the path below /api/v1', async () => {
    const request = vi.fn(
      async (config: AxiosRequestConfig): Promise<AxiosResponse> =>
        ({ data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }) as AxiosResponse,
    );
    hostRequests(request);

    const response = await api.post('/tasks', { title: 'a' }, { params: { page: 2 } });

    expect(response.data).toEqual({ ok: true });
    expect(request).toHaveBeenCalledOnce();
    const sent = request.mock.calls[0][0];
    expect(sent.method).toBe('post');
    expect(sent.url).toBe('/tasks');
    expect(sent.params).toEqual({ page: 2 });
    expect(JSON.parse(sent.data as string)).toEqual({ title: 'a' });
    expect(sent.headers).toEqual({ 'Content-Type': 'application/json' });
  });

  it('leaves a 401 to the caller instead of refreshing the session', async () => {
    const unauthorized = Object.assign(new Error('401'), { response: { status: 401 }, config: {} });
    hostRequests(async () => {
      throw unauthorized;
    });

    await expect(api.get('/tasks')).rejects.toBe(unauthorized);
  });
});
