import { create } from 'zustand';
import api from '@/lib/axios';

export interface OptionalComponent {
  name: string;
  enabled: boolean;
  [key: string]: any;
}

interface ComponentsState {
  components: OptionalComponent[];
  loading: boolean;
  error: string | null;

  // Actions
  fetchComponents: () => Promise<void>;
  toggleComponent: (name: string, enabled: boolean) => Promise<void>;
}

export const useComponentsStore = create<ComponentsState>((set) => ({
  components: [],
  loading: false,
  error: null,

  fetchComponents: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get('/optional-components');
      set({ components: response.data, loading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch components',
        loading: false
      });
    }
  },

  toggleComponent: async (name, enabled) => {
    try {
      await api.patch(`/optional-components/${name}`, { enabled });
      set(state => ({
        components: state.components.map(c =>
          c.name === name ? { ...c, enabled } : c
        )
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Failed to toggle component' });
      throw error;
    }
  },
}));
