import { create } from 'zustand';
import api from '@/lib/axios';

export interface Service {
  name: string;
  status: string;
  health: string;
  [key: string]: any;
}

interface ServicesState {
  services: Service[];
  loading: boolean;
  error: string | null;

  // Actions
  fetchServices: () => Promise<void>;
  updateService: (name: string, updates: Partial<Service>) => Promise<void>;
  deleteService: (name: string) => Promise<void>;
}

export const useServicesStore = create<ServicesState>((set, get) => ({
  services: [],
  loading: false,
  error: null,

  fetchServices: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get('/services');
      set({ services: response.data, loading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch services',
        loading: false
      });
    }
  },

  updateService: async (name, updates) => {
    try {
      await api.patch(`/services/${name}`, updates);
      set(state => ({
        services: state.services.map(s =>
          s.name === name ? { ...s, ...updates } : s
        )
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Failed to update service' });
      throw error;
    }
  },

  deleteService: async (name) => {
    try {
      await api.delete(`/services/${name}`);
      set(state => ({
        services: state.services.filter(s => s.name !== name)
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Failed to delete service' });
      throw error;
    }
  },
}));
