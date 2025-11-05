import { create } from 'zustand';
import { getUserInfo, logout as authLogout, UserInfo } from '@/lib/auth';
import { getToken, clearTokens } from '@/lib/tokenManager';

interface AuthState {
  user: UserInfo | null;
  loading: boolean;
  error: string | null;

  // Computed
  isAuthenticated: () => boolean;
  userRoles: () => string[];
  hasRole: (role: string) => boolean;

  // Actions
  setUser: (user: UserInfo | null) => void;
  clearAuth: () => void;
  fetchUser: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  loading: false,
  error: null,

  // Computed values
  isAuthenticated: () => {
    const token = getToken();
    return !!token && !!get().user;
  },

  userRoles: () => {
    return get().user?.roles || [];
  },

  hasRole: (role: string) => {
    return get().userRoles().includes(role);
  },

  // Actions
  setUser: (user) => {
    set({ user });
  },

  clearAuth: () => {
    clearTokens();
    set({ user: null, error: null });
  },

  fetchUser: async () => {
    set({ loading: true, error: null });
    try {
      const token = getToken();
      if (!token) {
        throw new Error('No authentication token');
      }

      const userInfo = await getUserInfo();
      set({ user: userInfo, loading: false });
    } catch (err) {
      console.error('Failed to fetch user:', err);
      set({
        error: err instanceof Error ? err.message : 'Failed to fetch user',
        user: null,
        loading: false
      });
      throw err;
    }
  },

  logout: async () => {
    set({ user: null });
    await authLogout();
  },
}));
