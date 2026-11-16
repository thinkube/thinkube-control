/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { create } from 'zustand';
import { getUserInfo, logout as authLogout, UserInfo } from '@/lib/auth';
import { getToken, clearTokens } from '@/lib/tokenManager';

interface AuthState {
  user: UserInfo | null;
  loading: boolean;
  error: string | null;

  isAuthenticated: () => boolean;
  userRoles: () => string[];
  hasRole: (role: string) => boolean;

  setUser: (user: UserInfo | null) => void;
  clearAuth: () => void;
  fetchUser: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  loading: false,
  error: null,

  isAuthenticated: () => !!getToken() && !!get().user,

  userRoles: () => get().user?.roles || [],

  hasRole: (role) => get().userRoles().includes(role),

  setUser: (user) => set({ user }),

  clearAuth: () => {
    clearTokens();
    set({ user: null, error: null });
  },

  fetchUser: async () => {
    set({ loading: true, error: null });
    try {
      if (!getToken()) {
        throw new Error('No authentication token');
      }

      const userInfo = await getUserInfo();
      set({ user: userInfo, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : 'Failed to fetch user',
        user: null,
        loading: false,
      });
      throw err;
    }
  },

  logout: async () => {
    set({ user: null });
    await authLogout();
  },
}));
