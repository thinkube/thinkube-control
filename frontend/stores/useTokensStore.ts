import { create } from 'zustand';
import api from '@/lib/axios';

export interface APIToken {
  id: string;
  name: string;
  token?: string;
  created_at: string;
  [key: string]: any;
}

interface TokensState {
  tokens: APIToken[];
  loading: boolean;
  error: string | null;

  // Actions
  fetchTokens: () => Promise<void>;
  createToken: (name: string) => Promise<APIToken>;
  deleteToken: (id: string) => Promise<void>;
}

export const useTokensStore = create<TokensState>((set) => ({
  tokens: [],
  loading: false,
  error: null,

  fetchTokens: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get('/tokens');
      set({ tokens: response.data, loading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch tokens',
        loading: false
      });
    }
  },

  createToken: async (name) => {
    try {
      const response = await api.post('/tokens', { name });
      set(state => ({
        tokens: [...state.tokens, response.data]
      }));
      return response.data;
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Failed to create token' });
      throw error;
    }
  },

  deleteToken: async (id) => {
    try {
      await api.delete(`/tokens/${id}`);
      set(state => ({
        tokens: state.tokens.filter(t => t.id !== id)
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Failed to delete token' });
      throw error;
    }
  },
}));
