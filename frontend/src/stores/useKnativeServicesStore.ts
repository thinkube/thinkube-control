import { create } from 'zustand';
import api from '@/lib/axios';

export interface KnativeService {
  name: string;
  namespace: string;
  url: string | null;
  status: string;
  ready_condition: string | null;
  latest_revision: string | null;
  min_scale: number;
  max_scale: number;
  container_concurrency: number;
  timeout_seconds: number;
  current_replicas: number;
  image: string | null;
  created_at: string | null;
  last_transition: string | null;
}

interface KnativeServicesState {
  services: KnativeService[];
  loading: boolean;
  error: string | null;
  fetchServices: () => Promise<void>;
  deleteService: (namespace: string, name: string) => Promise<void>;
}

export const useKnativeServicesStore = create<KnativeServicesState>((set, get) => ({
  services: [],
  loading: false,
  error: null,

  fetchServices: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get('/knative-services');
      set({ services: response.data.services, loading: false });
    } catch (err: any) {
      set({ error: err.message || 'Failed to fetch Knative services', loading: false });
    }
  },

  deleteService: async (namespace: string, name: string) => {
    try {
      await api.delete(`/knative-services/${namespace}/${name}`);
      // Refresh the list
      await get().fetchServices();
    } catch (err: any) {
      throw err;
    }
  },
}));
