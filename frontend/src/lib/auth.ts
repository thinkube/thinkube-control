import api from './axios';
import { storeTokens, clearTokens, getToken, getRefreshToken, isTokenExpired, Tokens } from './tokenManager';

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

/**
 * Get authentication configuration from backend
 */
export const getAuthConfig = async (): Promise<AuthConfig> => {
  const response = await api.get('/auth/auth-config');
  return response.data;
};

/**
 * Build authorization URL for Keycloak login
 */
export const getAuthorizationUrl = async (): Promise<string> => {
  const config = await getAuthConfig();

  if (!config.auth_url || !config.client_id) {
    console.error('Invalid auth config:', config);
    throw new Error('Invalid authentication configuration');
  }

  const params = new URLSearchParams({
    client_id: config.client_id,
    redirect_uri: `${window.location.origin}/auth/callback`,
    response_type: 'code',
    scope: 'openid profile email'
  });

  return `${config.auth_url}?${params.toString()}`;
};

/**
 * Handle OAuth2 callback - exchange code for token
 */
export const handleAuthCallback = async (code: string): Promise<Tokens> => {
  const redirectUri = `${window.location.origin}/auth/callback`;
  const response = await api.post('/auth/token', {
    code,
    redirect_uri: redirectUri
  });

  const tokens = response.data;
  storeTokens(tokens);
  return tokens;
};

/**
 * Get user information from backend
 */
export const getUserInfo = async (): Promise<UserInfo> => {
  const token = getToken();
  if (!token) {
    throw new Error('No access token available');
  }

  const response = await api.get('/auth/userinfo', {
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });

  return response.data;
};

/**
 * Check if user has a specific role
 */
export const hasRole = (userInfo: UserInfo | null, role: string): boolean => {
  if (!userInfo || !userInfo.roles) {
    return false;
  }
  return userInfo.roles.includes(role);
};

/**
 * Redirect to Keycloak login
 */
export const redirectToLogin = async (intendedRoute?: string): Promise<void> => {
  try {
    // Store the intended route if provided
    if (intendedRoute) {
      sessionStorage.setItem('intendedRoute', intendedRoute);
    }

    const authUrl = await getAuthorizationUrl();
    window.location.href = authUrl;
  } catch (error) {
    console.error('Failed to redirect to login:', error);
    throw error;
  }
};

/**
 * Log out the user
 */
export const logout = async (): Promise<void> => {
  clearTokens();

  try {
    const config = await getAuthConfig();
    const logoutUrl = `${config.logout_url}?redirect_uri=${encodeURIComponent(window.location.origin)}`;
    window.location.href = logoutUrl;
  } catch (error) {
    // If we can't get config, just redirect to home
    window.location.href = '/';
  }
};

/**
 * Check authentication status
 */
export const isAuthenticated = (): boolean => {
  const token = getToken();
  return !!(token && !isTokenExpired());
};

/**
 * Refresh access token using refresh token
 */
export const refreshToken = async (): Promise<Tokens> => {
  const refreshTokenValue = getRefreshToken();
  if (!refreshTokenValue) {
    throw new Error('No refresh token available');
  }

  const response = await api.post('/auth/refresh-token', {
    refresh_token: refreshTokenValue
  });

  const tokens = response.data;
  storeTokens(tokens);
  return tokens;
};
