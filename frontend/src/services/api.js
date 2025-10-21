// src/services/api.js
import axios from 'axios';
import { getToken, getRefreshToken, storeTokens, clearTokens, isTokenExpired } from './tokenManager';
import { redirectToLogin } from './auth';

// Base URL for API requests
const API_URL = '/api/v1';

// Setup axios defaults
axios.defaults.baseURL = API_URL;

// Track if we're already refreshing the token
let isRefreshing = false;
let failedQueue = [];

// Track active WebSocket connections (don't redirect during deployment)
let activeWebSockets = 0;

export const incrementWebSocketCount = () => {
  activeWebSockets++;
};

export const decrementWebSocketCount = () => {
  activeWebSockets = Math.max(0, activeWebSockets - 1);
};

const processQueue = (error, token = null) => {
  failedQueue.forEach(prom => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });

  failedQueue = [];
};

// Add a request interceptor to include the auth token
axios.interceptors.request.use(
  async (config) => {
    // Skip token refresh for auth endpoints
    const isAuthEndpoint = config.url?.includes('/auth/');

    // Check if token is expired before making request (skip for auth endpoints)
    if (!isAuthEndpoint && isTokenExpired() && !config._retry) {
      if (!isRefreshing) {
        isRefreshing = true;
        try {
          const refreshTokenValue = getRefreshToken();
          if (!refreshTokenValue) {
            throw new Error('No refresh token available');
          }
          const response = await axios.post('/auth/refresh-token', {
            refresh_token: refreshTokenValue
          });
          storeTokens(response.data);
          isRefreshing = false;
          processQueue(null, getToken());
        } catch (err) {
          isRefreshing = false;
          processQueue(err, null);
          clearTokens();
          // Only redirect if no active WebSocket connections
          if (activeWebSockets === 0) {
            redirectToLogin();
          } else {
            console.warn('Token refresh failed but keeping session active due to WebSocket connection');
          }
          return Promise.reject(err);
        }
      }

      // Wait for token refresh to complete
      return new Promise((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      }).then(token => {
        config.headers.Authorization = `Bearer ${getToken()}`;
        return config;
      });
    }

    const token = getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Add a response interceptor to handle errors
axios.interceptors.response.use(
  (response) => {
    return response;
  },
  async (error) => {
    const originalRequest = error.config;

    // If we get a 401 and haven't already tried to refresh
    if (error.response?.status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        // Wait for the token refresh to complete
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then(token => {
          originalRequest.headers.Authorization = `Bearer ${getToken()}`;
          return axios(originalRequest);
        }).catch(err => {
          return Promise.reject(err);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const refreshTokenValue = getRefreshToken();
        if (!refreshTokenValue) {
          throw new Error('No refresh token available');
        }
        const response = await axios.post('/auth/refresh-token', {
          refresh_token: refreshTokenValue
        });
        storeTokens(response.data);
        isRefreshing = false;
        processQueue(null, getToken());
        originalRequest.headers.Authorization = `Bearer ${getToken()}`;
        return axios(originalRequest);
      } catch (refreshError) {
        isRefreshing = false;
        processQueue(refreshError, null);
        clearTokens();
        // Only redirect to login if no active WebSocket connections
        if (activeWebSockets === 0) {
          window.location.href = '/login';
        } else {
          console.warn('Token refresh failed but keeping session active due to WebSocket connection');
        }
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

/**
 * Get all available dashboards
 */
export const getDashboards = async () => {
  try {
    const response = await axios.get('/dashboards/');
    return response.data;
  } catch (error) {
    console.error('Failed to get dashboards', error);
    throw error;
  }
};

/**
 * Get dashboard categories
 */
export const getDashboardCategories = async () => {
  try {
    const response = await axios.get('/dashboards/categories/');
    return response.data.categories;
  } catch (error) {
    console.error('Failed to get dashboard categories', error);
    throw error;
  }
};

/**
 * Get a specific dashboard by ID
 */
export const getDashboard = async (id) => {
  try {
    const response = await axios.get(`/dashboards/${id}`);
    return response.data;
  } catch (error) {
    console.error(`Failed to get dashboard with ID ${id}`, error);
    throw error;
  }
};

/**
 * Get authentication configuration
 */
export const getAuthConfig = async () => {
  try {
    const response = await axios.get('/auth/auth-config');
    return response.data;
  } catch (error) {
    console.error('Failed to get auth config', error);
    throw error;
  }
};

/**
 * Exchange authorization code for tokens
 */
export const exchangeCodeForToken = async (code, redirectUri) => {
  try {
    const response = await axios.post('/auth/token', {
      code,
      redirect_uri: redirectUri
    });
    return response.data;
  } catch (error) {
    console.error('Failed to exchange code for token', error);
    throw error;
  }
};

/**
 * Get user info from backend
 */
export const getUserInfo = async (token) => {
  try {
    const response = await axios.get('/auth/userinfo', {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    return response.data;
  } catch (error) {
    console.error('Failed to get user info', error);
    throw error;
  }
};

/**
 * Get all API tokens
 */
export const getTokens = async (token) => {
  try {
    const response = await axios.get('/tokens/', {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    return response.data;
  } catch (error) {
    console.error('Failed to get tokens', error);
    throw error;
  }
};

/**
 * Create a new API token
 */
export const createToken = async (tokenData, authToken) => {
  try {
    const response = await axios.post('/tokens/', tokenData, {
      headers: {
        'Authorization': `Bearer ${authToken}`
      }
    });
    return response.data;
  } catch (error) {
    console.error('Failed to create token', error);
    throw error;
  }
};

/**
 * Revoke an API token
 */
export const revokeToken = async (tokenId, authToken) => {
  try {
    await axios.delete(`/tokens/${tokenId}`, {
      headers: {
        'Authorization': `Bearer ${authToken}`
      }
    });
  } catch (error) {
    console.error('Failed to revoke token', error);
    throw error;
  }
};

/**
 * Reveal an API token value from Kubernetes secret
 */
export const revealToken = async (tokenId, authToken) => {
  try {
    const response = await axios.get(`/tokens/${tokenId}/reveal`, {
      headers: {
        'Authorization': `Bearer ${authToken}`
      }
    });
    return response.data;
  } catch (error) {
    console.error('Failed to reveal token', error);
    throw error;
  }
};

/**
 * Refresh authentication token
 */
export const refreshAuthToken = async (refreshToken) => {
  try {
    const response = await axios.post('/auth/refresh-token', {
      refresh_token: refreshToken
    });
    return response.data;
  } catch (error) {
    console.error('Failed to refresh token', error);
    throw error;
  }
};

/**
 * Deploy a template asynchronously
 */
export const deployTemplateAsync = async (templateData) => {
  try {
    const response = await axios.post('/templates/deploy-async', {
      ...templateData,
      execution_mode: 'websocket'  // UI always uses WebSocket mode
    });
    return response.data;
  } catch (error) {
    console.error('Failed to deploy template', error);
    throw error;
  }
};

/**
 * Get deployment status
 */
export const getDeploymentStatus = async (deploymentId) => {
  try {
    const response = await axios.get(`/templates/deployments/${deploymentId}`);
    return response.data;
  } catch (error) {
    console.error('Failed to get deployment status', error);
    throw error;
  }
};

/**
 * Get deployment logs
 */
export const getDeploymentLogs = async (deploymentId, offset = 0, limit = 100) => {
  try {
    const response = await axios.get(`/templates/deployments/${deploymentId}/logs`, {
      params: { offset, limit }
    });
    return response.data;
  } catch (error) {
    console.error('Failed to get deployment logs', error);
    throw error;
  }
};

/**
 * List deployments
 */
export const listDeployments = async (page = 1, pageSize = 20, status = null) => {
  try {
    const params = { page, page_size: pageSize };
    if (status) params.status = status;
    const response = await axios.get('/templates/deployments', { params });
    return response.data;
  } catch (error) {
    console.error('Failed to list deployments', error);
    throw error;
  }
};

// Export the configured axios instance for direct use
export { axios as api };