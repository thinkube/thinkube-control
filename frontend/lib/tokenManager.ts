// Token storage utilities - separate to avoid circular dependencies

const TOKEN_KEY = 'access_token';
const REFRESH_TOKEN_KEY = 'refresh_token';
const TOKEN_EXPIRY_KEY = 'token_expiry';

export interface Tokens {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
}

/**
 * Store authentication tokens in localStorage
 */
export const storeTokens = (tokens: Tokens): void => {
  localStorage.setItem(TOKEN_KEY, tokens.access_token);
  if (tokens.refresh_token) {
    localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  }
  // Calculate and store token expiry time
  const expiryTime = new Date().getTime() + (tokens.expires_in * 1000);
  localStorage.setItem(TOKEN_EXPIRY_KEY, expiryTime.toString());
};

/**
 * Clear stored tokens
 */
export const clearTokens = (): void => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(TOKEN_EXPIRY_KEY);
};

/**
 * Get stored access token
 */
export const getToken = (): string | null => {
  return localStorage.getItem(TOKEN_KEY);
};

/**
 * Get stored refresh token
 */
export const getRefreshToken = (): string | null => {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
};

/**
 * Check if token is expired
 */
export const isTokenExpired = (): boolean => {
  const expiryTime = localStorage.getItem(TOKEN_EXPIRY_KEY);
  if (!expiryTime) return true;
  return new Date().getTime() > parseInt(expiryTime);
};

/**
 * Get time until token expires (in milliseconds)
 * Returns 0 if token is expired or doesn't exist
 */
export const getTimeUntilExpiry = (): number => {
  const expiryTime = localStorage.getItem(TOKEN_EXPIRY_KEY);
  if (!expiryTime) return 0;
  const timeRemaining = parseInt(expiryTime) - new Date().getTime();
  return Math.max(0, timeRemaining);
};
