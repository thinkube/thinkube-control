// src/services/auth.js
import {
  getAuthConfig as getAuthConfigAPI,
  exchangeCodeForToken as exchangeCodeAPI,
  getUserInfo as getUserInfoAPI,
  refreshAuthToken as refreshAuthTokenAPI
} from './api';
import { storeTokens, clearTokens, getToken, getRefreshToken, isTokenExpired } from './tokenManager';

/**
 * Get authentication configuration from backend
 */
export const getAuthConfig = async () => {
  return getAuthConfigAPI();
};

/**
 * Build authorization URL for Keycloak login
 */
export const getAuthorizationUrl = async () => {
  const config = await getAuthConfig();
  console.log('Auth config received:', config);
  
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
export const handleAuthCallback = async (code) => {
  try {
    const redirectUri = `${window.location.origin}/auth/callback`;
    const tokens = await exchangeCodeAPI(code, redirectUri);
    
    storeTokens(tokens);
    return tokens;
  } catch (error) {
    console.error('Failed to exchange code for token', error);
    throw error;
  }
};

/**
 * Get user information from backend
 */
export const getUserInfo = async () => {
  try {
    const token = getToken();
    if (!token) {
      throw new Error('No access token available');
    }
    
    console.log('Getting user info with token:', token.substring(0, 20) + '...');
    
    const userInfo = await getUserInfoAPI(token);
    
    console.log('User info response:', userInfo);
    return userInfo;
  } catch (error) {
    console.error('Failed to get user info', error);
    console.error('Error response:', error.response?.data);
    console.error('Error status:', error.response?.status);
    
    // Don't automatically redirect on error - let the component handle it
    // This prevents redirect loops
    throw error;
  }
};

/**
 * Check if user has a specific role
 */
export const hasRole = (userInfo, role) => {
  if (!userInfo || !userInfo.roles) {
    return false;
  }
  
  return userInfo.roles.includes(role);
};

/**
 * Redirect to Keycloak login
 */
export const redirectToLogin = async (intendedRoute = null) => {
  try {
    // Store the intended route if provided
    if (intendedRoute) {
      sessionStorage.setItem('intendedRoute', intendedRoute);
    }

    const authUrl = await getAuthorizationUrl();
    window.location.href = authUrl;
  } catch (error) {
    console.error('Failed to redirect to login:', error);
    // Show the error to the user instead of redirecting to undefined
    alert('Authentication configuration error. Please check the console for details.');
    throw error;
  }
};

/**
 * Log out the user
 */
export const logout = async () => {
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
export const isAuthenticated = () => {
  const token = getToken();
  return token && !isTokenExpired();
};

/**
 * Refresh access token using refresh token
 */
export const refreshToken = async () => {
  const refreshTokenValue = getRefreshToken();
  if (!refreshTokenValue) {
    throw new Error('No refresh token available');
  }

  try {
    const tokens = await refreshAuthTokenAPI(refreshTokenValue);
    storeTokens(tokens);
    return tokens;
  } catch (error) {
    console.error('Failed to refresh token', error);
    clearTokens();
    throw error;
  }
};