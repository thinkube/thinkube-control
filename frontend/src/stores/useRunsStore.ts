/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { create } from 'zustand';
import api from '@/lib/axios';

export interface RunEntry {
  id: string;
  name: string;
  kind: string;
  status: string;
  queue_position: number | null;
  output: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

interface RunsState {
  active: RunEntry | null;
  queued: RunEntry[];
  recent: RunEntry[];
  dialogOpen: boolean;

  fetchRuns: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
  openDialog: () => void;
  setDialogOpen: (open: boolean) => void;
  removeQueued: (id: string) => Promise<void>;
  // True while a run with this name is queued or in progress.
  isInQueue: (name: string) => boolean;
}

let pollTimer: ReturnType<typeof setInterval> | null = null;
let pollers = 0;

/**
 * The run queue on the server: deployment runs start one at a time, and the
 * others wait. Any page can queue a run and open the runs dialog to follow it.
 */
export const useRunsStore = create<RunsState>((set, get) => ({
  active: null,
  queued: [],
  recent: [],
  dialogOpen: false,

  fetchRuns: async () => {
    try {
      const { data } = await api.get('/runs');
      set({ active: data.active, queued: data.queued, recent: data.recent });
    } catch (err) {
      console.error('Failed to read the run queue:', err);
    }
  },

  startPolling: () => {
    pollers += 1;
    if (pollTimer === null) {
      pollTimer = setInterval(() => get().fetchRuns(), 3000);
    }
  },

  stopPolling: () => {
    pollers = Math.max(0, pollers - 1);
    if (pollers === 0 && pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  },

  openDialog: () => {
    set({ dialogOpen: true });
    get().fetchRuns();
  },

  setDialogOpen: (open: boolean) => set({ dialogOpen: open }),

  removeQueued: async (id: string) => {
    await api.delete(`/runs/${id}`);
    await get().fetchRuns();
  },

  isInQueue: (name: string) => {
    const { active, queued } = get();
    return active?.name === name || queued.some(run => run.name === name);
  },
}));
