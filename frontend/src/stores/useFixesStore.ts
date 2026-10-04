/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { create } from 'zustand';
import api from '@/lib/axios';

export interface NewsEntry {
  id: string;
  date: string;
  title: string;
  body: string;
  read: boolean;
}

export type FixStatus = 'applied' | 'available' | 'not_installed' | 'apply_from_ide';

export interface FixEntry {
  id: string;
  date: string;
  title: string;
  body: string;
  severity: 'security' | 'bug';
  component: string;
  kind: 'core' | 'optional';
  fixed_in: string;
  commit: string;
  playbook: string;
  status: FixStatus;
  installed_version: string | null;
  ide_command: string | null;
}

export interface FixesFeed {
  url: string | null;
  last_checked: string | null;
  last_error: string | null;
  feed_fetched: string | null;
  feed_commit: string | null;
  news: NewsEntry[];
  fixes: FixEntry[];
  pending_fixes: number;
  unread_news: number;
}

interface FixesState {
  feed: FixesFeed | null;
  // The failure of the last request to thinkube-control itself.
  error: string | null;
  checking: boolean;

  fetchFeed: () => Promise<void>;
  checkNow: () => Promise<void>;
  applyFix: (id: string) => Promise<{ queue_position: number | null }>;
  markRead: (id: string) => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

let pollTimer: ReturnType<typeof setInterval> | null = null;
let pollers = 0;

const detail = (err: any) => err.response?.data?.detail ?? err.message;

/**
 * News and fixes from thinkube-fixes. The backend reads the feed every six
 * hours; the page and the top bar read the backend's copy every five minutes.
 */
export const useFixesStore = create<FixesState>((set, get) => ({
  feed: null,
  error: null,
  checking: false,

  fetchFeed: async () => {
    try {
      const { data } = await api.get('/fixes');
      set({ feed: data, error: null });
    } catch (err: any) {
      set({ error: detail(err) });
    }
  },

  checkNow: async () => {
    set({ checking: true });
    try {
      const { data } = await api.post('/fixes/check');
      set({ feed: data, error: null });
    } catch (err: any) {
      set({ error: detail(err) });
    } finally {
      set({ checking: false });
    }
  },

  applyFix: async (id: string) => {
    const { data } = await api.post(`/fixes/${id}/apply`);
    return data;
  },

  markRead: async (id: string) => {
    try {
      const { data } = await api.post(`/fixes/news/${id}/read`);
      set({ feed: data, error: null });
    } catch (err: any) {
      set({ error: detail(err) });
    }
  },

  startPolling: () => {
    pollers += 1;
    if (pollTimer === null) {
      pollTimer = setInterval(() => get().fetchFeed(), 5 * 60 * 1000);
    }
  },

  stopPolling: () => {
    pollers = Math.max(0, pollers - 1);
    if (pollers === 0 && pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  },
}));
