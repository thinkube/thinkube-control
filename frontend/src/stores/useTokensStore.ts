import { create } from 'zustand';
import api from '@/lib/axios';

export interface APIToken {
  id: string;
  name: string;
  token?: string;
  scopes?: string[];
  expires_at?: string;
  created_at: string;
  [key: string]: any;
}

interface TokenData {
  name: string;
  scopes?: string[];
  expires_at?: string;
}

interface TokensState {
  tokens: APIToken[];
  loading: boolean;
  error: string | null;

  // Actions
  fetchTokens: () => Promise<void>;
  createToken: (tokenData: TokenData) => Promise<APIToken>;
  revokeToken: (tokenId: string) => Promise<void>;
  revealToken: (tokenId: string) => Promise<any>;
}

export const useTokensStore = create<TokensState>((set, get) => ({
  tokens: [],
  loading: false,
  error: null,

  fetchTokens: async () => {
    set({ loading: true, error: null });

    try {
      const response = await api.get('/tokens');
      set({ tokens: response.data, loading: false });
    } catch (err: any) {
      console.error('Failed to fetch tokens:', err);
      set({
        error: err.message,
        loading: false
      });
    }
  },

  createToken: async (tokenData: TokenData) => {
    try {
      const response = await api.post('/tokens', tokenData);

      // Refresh the token list
      await get().fetchTokens();

      return response.data;
    } catch (err: any) {
      console.error('Failed to create token:', err);
      throw err;
    }
  },

  revokeToken: async (tokenId: string) => {
    try {
      await api.delete(`/tokens/${tokenId}`);

      // Refresh the token list
      await get().fetchTokens();
    } catch (err: any) {
      console.error('Failed to revoke token:', err);
      throw err;
    }
  },

  revealToken: async (tokenId: string) => {
    try {
      const response = await api.get(`/tokens/${tokenId}/reveal`);
      return response.data;
    } catch (err: any) {
      console.error('Failed to reveal token:', err);
      throw err;
    }
  }
}));
