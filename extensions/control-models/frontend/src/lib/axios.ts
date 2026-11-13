/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import axios, { AxiosError, InternalAxiosRequestConfig, type AxiosRequestConfig, type AxiosResponse } from 'axios';
import { getToken, storeTokens, clearTokens, getRefreshToken } from './tokenManager';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

type RetriableRequest = InternalAxiosRequestConfig & { _retry?: boolean };

// While one refresh is in flight, other 401s wait for its result instead of
// starting a refresh of their own.
let isRefreshing = false;
let refreshSubscribers: ((token: string) => void)[] = [];

function subscribeTokenRefresh(cb: (token: string) => void) {
  refreshSubscribers.push(cb);
}

function onTokenRefreshed(token: string) {
  refreshSubscribers.forEach((cb) => cb(token));
  refreshSubscribers = [];
}

api.interceptors.request.use(
  (config) => {
    const token = getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    // Inside Thinkube Control, Control keeps the session: a 401 is the caller's to show
    if (hosted) {
      return Promise.reject(error);
    }
    const originalRequest = error.config as RetriableRequest | undefined;

    if (!originalRequest || originalRequest._retry || originalRequest.url?.includes('/auth/')) {
      return Promise.reject(error);
    }

    if (error.response?.status !== 401) {
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise((resolve) => {
        subscribeTokenRefresh((token: string) => {
          originalRequest.headers.Authorization = `Bearer ${token}`;
          resolve(api(originalRequest));
        });
      });
    }

    originalRequest._retry = true;
    isRefreshing = true;

    try {
      const refreshToken = getRefreshToken();
      if (!refreshToken) {
        throw new Error('No refresh token available');
      }

      const response = await axios.post('/api/v1/auth/refresh-token', {
        refresh_token: refreshToken,
      });

      const tokens = response.data;
      storeTokens(tokens);
      onTokenRefreshed(tokens.access_token);

      originalRequest.headers.Authorization = `Bearer ${tokens.access_token}`;
      return api(originalRequest);
    } catch (refreshError) {
      clearTokens();
      window.location.href = '/login';
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  },
);

let hosted = false;

/** Inside Thinkube Control: every call goes through `request`, Control's
 * client, which sends it to this app's /api/v1 with the user's session. */
export function hostRequests(request: (config: AxiosRequestConfig) => Promise<AxiosResponse>) {
  hosted = true;
  api.defaults.adapter = (config) =>
    request({
      method: config.method,
      url: config.url,
      params: config.params,
      data: config.data,
      // A FormData body carries its own multipart boundary, which the browser writes
      headers:
        config.headers?.['Content-Type'] && !(config.data instanceof FormData)
          ? { 'Content-Type': String(config.headers['Content-Type']) }
          : undefined,
      timeout: config.timeout,
    });
}

export default api;
