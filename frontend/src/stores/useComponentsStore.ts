import { create } from 'zustand';
import api from '@/lib/axios';

export interface OptionalComponent {
  name: string;
  category: string;
  installed: boolean;
  [key: string]: any;
}

interface ComponentsState {
  components: OptionalComponent[];
  loading: boolean;
  error: string | null;

  // Computed getters
  getInstalledComponents: () => OptionalComponent[];
  getAvailableComponents: () => OptionalComponent[];
  getComponentsByCategory: () => Record<string, OptionalComponent[]>;

  // Actions
  listComponents: () => Promise<any>;
  getComponentInfo: (componentName: string) => Promise<any>;
  installComponent: (componentName: string, parameters?: Record<string, any>) => Promise<any>;
  uninstallComponent: (componentName: string) => Promise<any>;
  getComponentStatus: (componentName: string) => Promise<any>;
}

export const useComponentsStore = create<ComponentsState>((set, get) => ({
  components: [],
  loading: false,
  error: null,

  // Computed getters
  getInstalledComponents: () => {
    const { components } = get();
    return components.filter(c => c.installed);
  },

  getAvailableComponents: () => {
    const { components } = get();
    return components.filter(c => !c.installed);
  },

  getComponentsByCategory: () => {
    const { components } = get();
    const grouped: Record<string, OptionalComponent[]> = {};
    components.forEach(component => {
      if (!grouped[component.category]) {
        grouped[component.category] = [];
      }
      grouped[component.category].push(component);
    });
    return grouped;
  },

  // Actions
  listComponents: async () => {
    set({ loading: true, error: null });

    try {
      const response = await api.get('/optional-components/list');
      set({ components: response.data.components, loading: false });
      return response.data;
    } catch (err: any) {
      console.error('Failed to list optional components:', err);
      set({
        error: err.message,
        loading: false
      });
      throw err;
    }
  },

  getComponentInfo: async (componentName: string) => {
    try {
      const response = await api.get(`/optional-components/${componentName}/info`);
      return response.data;
    } catch (err) {
      console.error(`Failed to get info for component ${componentName}:`, err);
      throw err;
    }
  },

  installComponent: async (componentName: string, parameters: Record<string, any> = {}) => {
    try {
      const response = await api.post(
        `/optional-components/${componentName}/install`,
        {
          parameters,
          force: false
        }
      );

      // Refresh component list after installation starts
      setTimeout(() => get().listComponents(), 2000);

      return response.data;
    } catch (err) {
      console.error(`Failed to install component ${componentName}:`, err);
      throw err;
    }
  },

  uninstallComponent: async (componentName: string) => {
    try {
      const response = await api.delete(`/optional-components/${componentName}`);

      // Refresh component list after uninstallation starts
      setTimeout(() => get().listComponents(), 2000);

      return response.data;
    } catch (err) {
      console.error(`Failed to uninstall component ${componentName}:`, err);
      throw err;
    }
  },

  getComponentStatus: async (componentName: string) => {
    try {
      const response = await api.get(`/optional-components/${componentName}/status`);
      return response.data;
    } catch (err) {
      console.error(`Failed to get status for component ${componentName}:`, err);
      throw err;
    }
  }
}));
