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

export interface NetworkDiscoveredNode {
  ip: string;
  hostname: string | null;
  zerotier_ip: string | null;
  zerotier_node_id: string | null;
  ssh_available: boolean;
  ssh_banner: string | null;
  is_ubuntu: boolean;
  confidence: 'confirmed' | 'possible' | 'failed';
  selected: boolean;
  ssh_status?: 'untested' | 'key_ok' | 'key_distributed' | 'needs_password' | 'failed';
  ssh_error?: string;
  hardware?: DiscoveredNode;
  validation?: {
    valid: boolean;
    errors: string[];
    warnings: string[];
  };
}

interface NodesState {
  nodes: ClusterNode[];
  architectures: string[];
  loading: boolean;
  error: string | null;

  // Legacy single-node discovery
  discoveredNode: DiscoveredNode | null;
  discovering: boolean;

  // Network discovery (new wizard)
  networkNodes: NetworkDiscoveredNode[];
  networkScanning: boolean;
  networkMode: string | null;
  sshVerifying: boolean;
  hardwareDetecting: boolean;

  // Actions
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

  // New wizard actions
  discoverNetwork: (scanCidrs?: string[]) => Promise<void>;
  toggleNodeSelection: (ip: string) => void;
  selectAllNodes: () => void;
  deselectAllNodes: () => void;
  verifySSH: (password?: string) => Promise<void>;
  detectHardwareBatch: () => Promise<void>;
  addNodesBatch: (password?: string) => Promise<{ job_id: string } | null>;
  clearNetworkNodes: () => void;
}

export const useNodesStore = create<NodesState>((set, get) => ({
  nodes: [],
  architectures: [],
  loading: false,
  error: null,
  discoveredNode: null,
  discovering: false,
  networkNodes: [],
  networkScanning: false,
  networkMode: null,
  sshVerifying: false,
  hardwareDetecting: false,

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

  discoverNetwork: async (scanCidrs?: string[]) => {
    set({ networkScanning: true, networkNodes: [], error: null });
    try {
      const response = await api.post('/nodes/discover-network', {
        scan_cidrs: scanCidrs?.length ? scanCidrs : undefined,
      });
      const nodes: NetworkDiscoveredNode[] = (response.data.nodes || []).map(
        (n: any) => ({
          ...n,
          selected: false,
          ssh_status: 'untested' as const,
        })
      );
      set({
        networkNodes: nodes,
        networkMode: response.data.network_mode,
        networkScanning: false,
      });
    } catch (err: any) {
      console.error('Network discovery failed:', err);
      set({
        error: err.response?.data?.detail || err.message,
        networkScanning: false,
      });
    }
  },

  toggleNodeSelection: (ip: string) => {
    set((state) => ({
      networkNodes: state.networkNodes.map((n) =>
        n.ip === ip ? { ...n, selected: !n.selected } : n
      ),
    }));
  },

  selectAllNodes: () => {
    set((state) => ({
      networkNodes: state.networkNodes.map((n) => ({ ...n, selected: true })),
    }));
  },

  deselectAllNodes: () => {
    set((state) => ({
      networkNodes: state.networkNodes.map((n) => ({ ...n, selected: false })),
    }));
  },

  verifySSH: async (password?: string) => {
    const { networkNodes } = get();
    const selected = networkNodes.filter((n) => n.selected);
    if (selected.length === 0) return;

    set({ sshVerifying: true, error: null });
    try {
      const response = await api.post('/nodes/verify-ssh', {
        nodes: selected.map((n) => ({ ip: n.ip })),
        password: password || undefined,
      });

      const results: Array<{ ip: string; ssh_status: NetworkDiscoveredNode['ssh_status']; error?: string }> =
        response.data.results || [];
      const resultMap = new Map(results.map((r) => [r.ip, r]));

      set((state) => ({
        networkNodes: state.networkNodes.map((n): NetworkDiscoveredNode => {
          const r = resultMap.get(n.ip);
          if (!r) return n;
          return {
            ...n,
            ssh_status: r.ssh_status,
            ssh_error: r.error,
          };
        }),
        sshVerifying: false,
      }));
    } catch (err: any) {
      console.error('SSH verification failed:', err);
      set({
        error: err.response?.data?.detail || err.message,
        sshVerifying: false,
      });
    }
  },

  detectHardwareBatch: async () => {
    const { networkNodes } = get();
    const selected = networkNodes.filter((n) => n.selected);
    if (selected.length === 0) return;

    set({ hardwareDetecting: true, error: null });
    try {
      const response = await api.post('/nodes/detect-hardware-batch', {
        nodes: selected.map((n) => ({ ip: n.ip })),
      });

      const results: DiscoveredNode[] = response.data.results || [];
      const resultByIp = new Map(results.map((r) => [r.ip, r]));

      set((state) => ({
        networkNodes: state.networkNodes.map((n): NetworkDiscoveredNode => {
          const hw = resultByIp.get(n.ip);
          if (!hw) return n;
          return {
            ...n,
            hardware: hw,
            hostname: hw.hostname || n.hostname,
            validation: (hw as any).validation,
          };
        }),
        hardwareDetecting: false,
      }));
    } catch (err: any) {
      console.error('Hardware detection failed:', err);
      set({
        error: err.response?.data?.detail || err.message,
        hardwareDetecting: false,
      });
    }
  },

  addNodesBatch: async (password?: string) => {
    const { networkNodes } = get();
    const selected = networkNodes.filter((n) => n.selected && n.hardware);
    if (selected.length === 0) return null;

    try {
      const nodesPayload = selected.map((n) => ({
        ip: n.ip,
        hostname: n.hardware?.hostname || n.hostname || '',
        lan_ip: n.ip,
        zerotier_ip: n.zerotier_ip || undefined,
        architecture: n.hardware?.architecture || '',
        gpu_detected: n.hardware?.gpu_detected || false,
        gpu_count: n.hardware?.gpu_count || 0,
        gpu_model: n.hardware?.gpu_model || '',
      }));

      const response = await api.post('/nodes/add-batch', {
        nodes: nodesPayload,
        password: password || undefined,
      });
      return response.data;
    } catch (err: any) {
      console.error('Failed to initiate batch node addition:', err);
      set({ error: err.response?.data?.detail || err.message });
      return null;
    }
  },

  clearNetworkNodes: () =>
    set({
      networkNodes: [],
      networkMode: null,
      error: null,
    }),
}));
