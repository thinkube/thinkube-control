import { create } from 'zustand';
import api from '@/lib/axios';

export interface Service {
  id: string;
  name: string;
  display_name?: string;
  description?: string;
  type: 'core' | 'optional' | 'user_app';
  category?: string;
  is_enabled: boolean;
  is_favorite: boolean;
  can_be_disabled?: boolean;
  url?: string;
  icon?: string;
  gpu_count?: number;
  gpu_nodes?: string[];
  component_version?: string;
  latest_health?: {
    status: string;
    checked_at: string;
  };
}

interface ServiceDetails extends Service {
  // Extended service details
  [key: string]: any;
}

interface HealthData {
  // Health history data
  [key: string]: any;
}

interface ServicesState {
  // State
  services: Service[];
  loading: boolean;
  error: string | null;
  selectedServiceId: string | null;
  serviceDetails: Record<string, ServiceDetails>;
  healthHistory: Record<string, HealthData>;
  favoriteServices: Service[];

  // Filters
  categoryFilter: string | null;
  enabledFilter: boolean | null;

  // Computed getters
  getFilteredServices: () => Service[];
  getCategories: () => string[];
  getSelectedService: () => Service | undefined;
  getCoreServices: () => Service[];
  getOptionalServices: () => Service[];
  getUserApps: () => Service[];
  getFavoriteServicesComputed: () => Service[];

  // Actions
  fetchServices: () => Promise<void>;
  fetchServiceDetails: (serviceId: string) => Promise<ServiceDetails>;
  toggleService: (serviceId: string, enabled: boolean, reason?: string | null) => Promise<Service>;
  restartService: (serviceId: string) => Promise<any>;
  fetchHealthHistory: (serviceId: string, hours?: number) => Promise<HealthData>;
  triggerHealthCheck: (serviceId: string) => Promise<any>;
  checkServiceName: (name: string, type: string) => Promise<any>;
  syncServices: () => Promise<any>;
  setCategoryFilter: (category: string | null) => void;
  setEnabledFilter: (enabled: boolean | null) => void;
  clearFilters: () => void;
  selectService: (serviceId: string | null) => void;
  fetchFavorites: () => Promise<any>;
  addToFavorites: (serviceId: string) => Promise<any>;
  removeFromFavorites: (serviceId: string) => Promise<void>;
  toggleFavorite: (service: Service) => Promise<void>;
  reorderFavorites: (serviceIds: string[]) => Promise<void>;
  describePod: (serviceId: string, podName: string) => Promise<any>;
  getContainerLogs: (serviceId: string, podName: string, containerName: string, lines?: number) => Promise<any>;
}

export const useServicesStore = create<ServicesState>((set, get) => ({
  // State
  services: [],
  loading: false,
  error: null,
  selectedServiceId: null,
  serviceDetails: {},
  healthHistory: {},
  favoriteServices: [],

  // Filters
  categoryFilter: null,
  enabledFilter: null,

  // Computed getters
  getFilteredServices: () => {
    const { services, categoryFilter, enabledFilter } = get();
    let result = services;

    if (categoryFilter) {
      result = result.filter(s => s.category === categoryFilter);
    }

    if (enabledFilter !== null) {
      result = result.filter(s => s.is_enabled === enabledFilter);
    }

    return result;
  },

  getCategories: () => {
    const { services } = get();
    const cats = new Set<string>();
    services.forEach(s => {
      if (s.category) cats.add(s.category);
    });
    return Array.from(cats).sort();
  },

  getSelectedService: () => {
    const { services, selectedServiceId } = get();
    return services.find(s => s.id === selectedServiceId);
  },

  getCoreServices: () => {
    const { services } = get();
    return services.filter(s => s.type === 'core');
  },

  getOptionalServices: () => {
    const { services } = get();
    return services.filter(s => s.type === 'optional');
  },

  getUserApps: () => {
    const { services } = get();
    return services.filter(s => s.type === 'user_app');
  },

  getFavoriteServicesComputed: () => {
    const { services } = get();
    return services.filter(s => s.is_favorite);
  },

  // Actions
  fetchServices: async () => {
    set({ loading: true, error: null });

    try {
      const response = await api.get('/services/');
      set({ services: response.data.services, loading: false });
    } catch (err: any) {
      console.error('Failed to fetch services:', err);
      set({
        error: err.response?.data?.detail || err.message,
        loading: false
      });
    }
  },

  fetchServiceDetails: async (serviceId: string) => {
    try {
      const response = await api.get(`/services/${serviceId}`);
      set(state => ({
        serviceDetails: {
          ...state.serviceDetails,
          [serviceId]: response.data
        }
      }));
      return response.data;
    } catch (err) {
      console.error('Failed to fetch service details:', err);
      throw err;
    }
  },

  toggleService: async (serviceId: string, enabled: boolean, reason: string | null = null) => {
    try {
      const response = await api.post(`/services/${serviceId}/toggle`, {
        is_enabled: enabled,
        reason
      });

      // Update service in list
      set(state => ({
        services: state.services.map(s =>
          s.id === serviceId ? response.data : s
        ),
        serviceDetails: state.serviceDetails[serviceId]
          ? { ...state.serviceDetails, [serviceId]: response.data }
          : state.serviceDetails
      }));

      return response.data;
    } catch (err) {
      console.error('Failed to toggle service:', err);
      throw err;
    }
  },

  restartService: async (serviceId: string) => {
    try {
      const response = await api.post(`/services/${serviceId}/restart`);

      // Trigger health check after restart
      setTimeout(() => {
        get().triggerHealthCheck(serviceId);
      }, 5000);

      return response.data;
    } catch (err) {
      console.error('Failed to restart service:', err);
      throw err;
    }
  },

  fetchHealthHistory: async (serviceId: string, hours: number = 24) => {
    try {
      const response = await api.get(`/services/${serviceId}/health`, {
        params: { hours }
      });
      set(state => ({
        healthHistory: {
          ...state.healthHistory,
          [serviceId]: response.data
        }
      }));
      return response.data;
    } catch (err) {
      console.error('Failed to fetch health history:', err);
      throw err;
    }
  },

  triggerHealthCheck: async (serviceId: string) => {
    try {
      const response = await api.post(`/services/${serviceId}/health-check`);

      // Update service health status if successful
      set(state => ({
        services: state.services.map(s =>
          s.id === serviceId
            ? {
                ...s,
                latest_health: {
                  status: response.data.status,
                  checked_at: response.data.checked_at || new Date().toISOString()
                }
              }
            : s
        )
      }));

      return response.data;
    } catch (err) {
      console.error('Failed to trigger health check:', err);
      throw err;
    }
  },

  checkServiceName: async (name: string, type: string) => {
    try {
      const response = await api.post('/services/check-name', { name, type });
      return response.data;
    } catch (err) {
      console.error('Failed to check service name:', err);
      throw err;
    }
  },

  syncServices: async () => {
    try {
      const response = await api.post('/services/sync');
      // Refresh services after sync
      await get().fetchServices();
      return response.data;
    } catch (err) {
      console.error('Failed to sync services:', err);
      throw err;
    }
  },

  setCategoryFilter: (category: string | null) => {
    set({ categoryFilter: category });
  },

  setEnabledFilter: (enabled: boolean | null) => {
    set({ enabledFilter: enabled });
  },

  clearFilters: () => {
    set({ categoryFilter: null, enabledFilter: null });
  },

  selectService: (serviceId: string | null) => {
    set({ selectedServiceId: serviceId });
  },

  fetchFavorites: async () => {
    try {
      const response = await api.get('/services/favorites');
      set({ favoriteServices: response.data.services });
      return response.data;
    } catch (err) {
      console.error('Failed to fetch favorites:', err);
      throw err;
    }
  },

  addToFavorites: async (serviceId: string) => {
    try {
      const response = await api.post(`/services/${serviceId}/favorite`);

      // Update the service in the main list
      set(state => ({
        services: state.services.map(s =>
          s.id === serviceId ? { ...s, is_favorite: true } : s
        )
      }));

      return response.data;
    } catch (err) {
      console.error('Failed to add to favorites:', err);
      throw err;
    }
  },

  removeFromFavorites: async (serviceId: string) => {
    try {
      await api.delete(`/services/${serviceId}/favorite`);

      // Update the service in the main list
      set(state => ({
        services: state.services.map(s =>
          s.id === serviceId ? { ...s, is_favorite: false } : s
        )
      }));
    } catch (err) {
      console.error('Failed to remove from favorites:', err);
      throw err;
    }
  },

  toggleFavorite: async (service: Service) => {
    if (service.is_favorite) {
      await get().removeFromFavorites(service.id);
    } else {
      await get().addToFavorites(service.id);
    }
  },

  reorderFavorites: async (serviceIds: string[]) => {
    const { services } = get();

    // Create a map of new positions
    const positionMap = new Map(serviceIds.map((id, index) => [id, index]));

    // Update services array with new favorite_order
    const updatedServices = services.map(service => {
      if (positionMap.has(service.id)) {
        return { ...service, favorite_order: positionMap.get(service.id)! };
      }
      return service;
    });

    // Optimistically update state
    set({ services: updatedServices });

    try {
      await api.put('/services/favorites/reorder', serviceIds);
    } catch (err) {
      console.error('Failed to reorder favorites:', err);
      // Revert on error by refetching
      get().fetchServices();
      throw err;
    }
  },

  describePod: async (serviceId: string, podName: string) => {
    try {
      const response = await api.get(`/services/${serviceId}/pods/${podName}/describe`);
      return response.data;
    } catch (err) {
      console.error('Failed to describe pod:', err);
      throw err;
    }
  },

  getContainerLogs: async (serviceId: string, podName: string, containerName: string, lines: number = 100) => {
    try {
      const response = await api.get(
        `/services/${serviceId}/pods/${podName}/containers/${containerName}/logs`,
        { params: { lines } }
      );
      return response.data;
    } catch (err) {
      console.error('Failed to get container logs:', err);
      throw err;
    }
  }
}));
