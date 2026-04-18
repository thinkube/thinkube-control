import { create } from 'zustand';
import api from '@/lib/axios';

export interface ClusterNode {
  name: string;
  architecture: string;
  os: string;
  role: 'control_plane' | 'worker';
  ready: boolean;
  cpu_capacity: number;
  memory_capacity_gb: number;
  gpu_count: number;
  kubelet_version: string;
  kernel_version: string;
  creation_timestamp: string | null;
  labels: Record<string, string>;
  is_build_node: boolean;
}

export interface DiscoveredNode {
  ip: string;
  hostname: string;
  architecture: string;
  normalized_arch: string;
  cpu_cores: number;
  memory_gb: number;
  disk_gb: number;
  os_release: string;
  k8s_installed: boolean;
  gpu_detected: boolean;
  gpu_model: string;
  gpu_count: number;
  nvidia_driver_version: string;
  error?: string;
}

interface NodesState {
  nodes: ClusterNode[];
  architectures: string[];
  loading: boolean;
  error: string | null;
  discoveredNode: DiscoveredNode | null;
  discovering: boolean;

  listNodes: () => Promise<void>;
  discoverNode: (ip: string, username?: string) => Promise<DiscoveredNode | null>;
  addNode: (params: {
    hostname: string;
    ip: string;
    architecture: string;
    zerotier_ip?: string;
    lan_ip?: string;
    gpu_detected?: boolean;
    gpu_count?: number;
    gpu_model?: string;
  }) => Promise<{ job_id: string } | null>;
  removeNode: (hostname: string, drain?: boolean) => Promise<boolean>;
  clearDiscoveredNode: () => void;
}

export const useNodesStore = create<NodesState>((set, get) => ({
  nodes: [],
  architectures: [],
  loading: false,
  error: null,
  discoveredNode: null,
  discovering: false,

  listNodes: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get('/nodes/list');
      set({
        nodes: response.data.nodes,
        architectures: response.data.architectures,
        loading: false,
      });
    } catch (err: any) {
      console.error('Failed to list nodes:', err);
      set({ error: err.message, loading: false });
    }
  },

  discoverNode: async (ip: string, username?: string) => {
    set({ discovering: true, error: null, discoveredNode: null });
    try {
      const response = await api.post('/nodes/discover', { ip, username });
      if (response.data.success) {
        set({ discoveredNode: response.data, discovering: false });
        return response.data;
      } else {
        set({ error: response.data.error, discovering: false });
        return null;
      }
    } catch (err: any) {
      console.error('Failed to discover node:', err);
      set({ error: err.message, discovering: false });
      return null;
    }
  },

  addNode: async (params) => {
    try {
      const response = await api.post('/nodes/add', params);
      return response.data;
    } catch (err: any) {
      console.error('Failed to initiate node addition:', err);
      set({ error: err.response?.data?.detail || err.message });
      return null;
    }
  },

  removeNode: async (hostname: string, drain = true) => {
    try {
      await api.post('/nodes/remove', { hostname, drain });
      await get().listNodes();
      return true;
    } catch (err: any) {
      console.error('Failed to remove node:', err);
      set({ error: err.response?.data?.detail || err.message });
      return false;
    }
  },

  clearDiscoveredNode: () => set({ discoveredNode: null, error: null }),
}));
