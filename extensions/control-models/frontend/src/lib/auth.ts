/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import api from './axios';
import {
  storeTokens,
  clearTokens,
  getToken,
  getRefreshToken,
  isTokenExpired,
  Tokens,
} from './tokenManager';

export interface AuthConfig {
  auth_url: string;
  client_id: string;
  logout_url: string;
}

export interface UserInfo {
  sub: string;
  email?: string;
  name?: string;
  preferred_username?: string;
  roles?: string[];
}

export const getAuthConfig = async (): Promise<AuthConfig> => {
  const response = await api.get('/auth/auth-config');
  return response.data;
};

/** Build the Keycloak authorization URL for the code flow. */
export const getAuthorizationUrl = async (): Promise<string> => {
  const config = await getAuthConfig();

  if (!config.auth_url || !config.client_id) {
    throw new Error('Invalid authentication configuration');
  }

  const params = new URLSearchParams({
    client_id: config.client_id,
    redirect_uri: `${window.location.origin}/auth/callback`,
    response_type: 'code',
    scope: 'openid profile email',
  });

  return `${config.auth_url}?${params.toString()}`;
};

/** Exchange the authorization code for tokens and store them. */
export const handleAuthCallback = async (code: string): Promise<Tokens> => {
  const response = await api.post('/auth/token', {
    code,
    redirect_uri: `${window.location.origin}/auth/callback`,
  });

  const tokens = response.data;
  storeTokens(tokens);
  return tokens;
};

export const getUserInfo = async (): Promise<UserInfo> => {
  const token = getToken();
  if (!token) {
    throw new Error('No access token available');
  }

  const response = await api.get('/auth/userinfo', {
    headers: { Authorization: `Bearer ${token}` },
  });

  return response.data;
};

export const hasRole = (userInfo: UserInfo | null, role: string): boolean =>
  !!userInfo?.roles?.includes(role);

/** Send the browser to Keycloak, remembering where the user was heading. */
export const redirectToLogin = async (intendedRoute?: string): Promise<void> => {
  if (intendedRoute) {
    sessionStorage.setItem('intendedRoute', intendedRoute);
  }

  const authUrl = await getAuthorizationUrl();
  window.location.href = authUrl;
};

export const logout = async (): Promise<void> => {
  clearTokens();

  try {
    const config = await getAuthConfig();
    window.location.href = `${config.logout_url}?redirect_uri=${encodeURIComponent(
      window.location.origin,
    )}`;
  } catch {
    // Without the config there is nowhere to send the user but home.
    window.location.href = '/';
  }
};

export const isAuthenticated = (): boolean => {
  const token = getToken();
  return !!(token && !isTokenExpired());
};

export const refreshToken = async (): Promise<Tokens> => {
  const refreshTokenValue = getRefreshToken();
  if (!refreshTokenValue) {
    throw new Error('No refresh token available');
  }

  const response = await api.post('/auth/refresh-token', {
    refresh_token: refreshTokenValue,
  });

  const tokens = response.data;
  storeTokens(tokens);
  return tokens;
};
