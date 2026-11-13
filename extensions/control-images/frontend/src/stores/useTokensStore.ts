/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { create } from 'zustand';
import api from '@/lib/axios';

export interface APIToken {
  id: string;
  name: string;
  created_at: string;
  expires_at?: string | null;
  last_used?: string | null;
  is_active: boolean;
  scopes: string[];
}

/** A freshly created token: the only time the secret itself is returned. */
export interface CreatedToken {
  id: string;
  name: string;
  token: string;
  expires_at?: string | null;
  scopes: string[];
}

export interface TokenInput {
  name: string;
  expires_in_days?: number | null;
  scopes?: string[];
}

interface TokensState {
  tokens: APIToken[];
  loading: boolean;
  error: string | null;

  fetchTokens: () => Promise<void>;
  createToken: (tokenData: TokenInput) => Promise<CreatedToken>;
  revokeToken: (tokenId: string) => Promise<void>;
}

export const useTokensStore = create<TokensState>((set, get) => ({
  tokens: [],
  loading: false,
  error: null,

  fetchTokens: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get<APIToken[]>('/tokens/');
      set({ tokens: response.data, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : 'Failed to fetch tokens',
        loading: false,
      });
      throw err;
    }
  },

  createToken: async (tokenData) => {
    const response = await api.post<CreatedToken>('/tokens/', {
      name: tokenData.name,
      expires_in_days: tokenData.expires_in_days ?? null,
      scopes: tokenData.scopes ?? [],
    });
    await get().fetchTokens();
    return response.data;
  },

  revokeToken: async (tokenId) => {
    await api.delete(`/tokens/${tokenId}`);
    await get().fetchTokens();
  },
}));
