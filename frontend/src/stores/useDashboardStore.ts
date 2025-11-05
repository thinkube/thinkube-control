import { create } from 'zustand';
import api from '@/lib/axios';

export interface DashboardData {
  services: number;
  running: number;
  stopped: number;
  [key: string]: any;
}

interface DashboardState {
  data: DashboardData | null;
  loading: boolean;
  error: string | null;

  // Actions
  fetchDashboardData: () => Promise<void>;
}

export const useDashboardStore = create<DashboardState>((set) => ({
  data: null,
  loading: false,
  error: null,

  fetchDashboardData: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get('/dashboard');
      set({ data: response.data, loading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch dashboard data',
        loading: false
      });
    }
  },
}));
