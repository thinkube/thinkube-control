import { create } from 'zustand';
import api from '@/lib/axios';

export interface Dashboard {
  name: string;
  category: string;
  [key: string]: any;
}

interface DashboardState {
  dashboards: Dashboard[];
  categories: string[];
  loading: boolean;
  error: string | null;
  selectedCategory: string | null;

  // Computed getters
  getFilteredDashboards: () => Dashboard[];

  // Actions
  fetchDashboards: () => Promise<void>;
  setSelectedCategory: (category: string | null) => void;
  clearSelectedCategory: () => void;
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  dashboards: [],
  categories: [],
  loading: false,
  error: null,
  selectedCategory: null,

  // Computed getters
  getFilteredDashboards: () => {
    const { dashboards, selectedCategory } = get();
    if (!selectedCategory) {
      return dashboards;
    }
    return dashboards.filter(d => d.category === selectedCategory);
  },

  // Actions
  fetchDashboards: async () => {
    set({ loading: true, error: null });

    try {
      const [dashboardsResponse, categoriesResponse] = await Promise.all([
        api.get('/dashboards'),
        api.get('/dashboards/categories')
      ]);

      set({
        dashboards: dashboardsResponse.data,
        categories: categoriesResponse.data,
        loading: false
      });
    } catch (err: any) {
      console.error('Failed to fetch dashboards:', err);
      set({
        error: err.message,
        loading: false
      });
    }
  },

  setSelectedCategory: (category: string | null) => {
    set({ selectedCategory: category });
  },

  clearSelectedCategory: () => {
    set({ selectedCategory: null });
  }
}));
