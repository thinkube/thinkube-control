import { create } from 'zustand';
import api from '@/lib/axios';
import { toast } from 'sonner';

export interface Model {
  id: string;
  name: string;
  size: string;
  quantization: string;
  description: string;
  server_type: string[];
  is_downloaded: boolean;
}

export interface MirrorJob {
  id: string;
  model_id: string;
  status: string;
  workflow_name: string | null;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
  is_running: boolean;
  is_complete: boolean;
  is_failed: boolean;
}

// Keep DownloadStatus as alias for backwards compatibility
export type DownloadStatus = MirrorJob;

interface ModelDownloadsState {
  // State
  models: Model[];
  downloads: DownloadStatus[];
  loading: boolean;
  error: string | null;
  pollingInterval: NodeJS.Timeout | null;

  // Computed getters
  getActiveDownloadsCount: () => number;
  getDownloadForModel: (modelId: string) => DownloadStatus | undefined;
  isModelDownloading: (modelId: string) => boolean;

  // Actions
  fetchModels: () => Promise<void>;
  startDownload: (modelId: string, hfToken?: string) => Promise<void>;
  fetchDownloads: () => Promise<void>;
  cancelDownload: (workflowId: string) => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

export const useModelDownloadsStore = create<ModelDownloadsState>((set, get) => ({
  // Initial state
  models: [],
  downloads: [],
  loading: false,
  error: null,
  pollingInterval: null,

  // Computed getters
  getActiveDownloadsCount: () => {
    return get().downloads.filter(d => d.is_running).length;
  },

  getDownloadForModel: (modelId: string) => {
    return get().downloads.find(d => d.model_id === modelId);
  },

  isModelDownloading: (modelId: string) => {
    const download = get().getDownloadForModel(modelId);
    return download ? download.is_running : false;
  },

  // Actions
  fetchModels: async () => {
    try {
      set({ loading: true, error: null });
      const response = await api.get('/models/catalog');
      set({ models: response.data.models, loading: false });
    } catch (error: any) {
      const errorMsg = error.response?.data?.detail || 'Failed to fetch models';
      set({ error: errorMsg, loading: false });
      toast.error(errorMsg);
    }
  },

  startDownload: async (modelId: string, hfToken?: string) => {
    try {
      set({ loading: true, error: null });

      const payload: any = { model_id: modelId };
      if (hfToken) {
        payload.hf_token = hfToken;
      }

      const response = await api.post('/models/mirrors', payload);

      const model = get().models.find(m => m.id === modelId);
      const modelName = model?.name || modelId;

      toast.success(`Mirror started: ${modelName}`);

      // Immediately fetch downloads to update UI
      await get().fetchDownloads();

      // Start polling if not already running
      if (!get().pollingInterval) {
        get().startPolling();
      }

      set({ loading: false });
    } catch (error: any) {
      const errorMsg = error.response?.data?.detail || 'Failed to start mirror';
      set({ error: errorMsg, loading: false });
      toast.error(errorMsg);
    }
  },

  fetchDownloads: async () => {
    try {
      const response = await api.get('/models/mirrors');
      const previousDownloads = get().downloads;
      const newDownloads: DownloadStatus[] = response.data.jobs;

      // Check for completed downloads to show notifications
      previousDownloads.forEach(prevDl => {
        const newDl = newDownloads.find(d => d.workflow_name === prevDl.workflow_name);

        // If download just completed
        if (prevDl.is_running && newDl && newDl.is_complete) {
          const model = get().models.find(m => m.id === newDl.model_id);
          const modelName = model?.name || newDl.model_id || 'Model';
          toast.success(`✓ ${modelName} mirrored successfully!`);
        }

        // If download just failed
        if (prevDl.is_running && newDl && newDl.is_failed) {
          const model = get().models.find(m => m.id === newDl.model_id);
          const modelName = model?.name || newDl.model_id || 'Model';
          toast.error(`✗ ${modelName} mirror failed`);
        }
      });

      set({ downloads: newDownloads });

      // Stop polling if no active downloads
      if (newDownloads.filter(d => d.is_running).length === 0) {
        get().stopPolling();
      }

    } catch (error: any) {
      console.error('Failed to fetch downloads:', error);
      // Don't show error toast for polling failures (less intrusive)
    }
  },

  cancelDownload: async (workflowId: string) => {
    try {
      await api.delete(`/models/mirrors/${workflowId}`);
      toast.success('Mirror cancelled');
      await get().fetchDownloads();
    } catch (error: any) {
      const errorMsg = error.response?.data?.detail || 'Failed to cancel mirror';
      toast.error(errorMsg);
    }
  },

  startPolling: () => {
    // Don't start if already polling
    if (get().pollingInterval) {
      return;
    }

    // Poll every 10 seconds
    const interval = setInterval(() => {
      get().fetchDownloads();
    }, 10000);

    set({ pollingInterval: interval });
  },

  stopPolling: () => {
    const interval = get().pollingInterval;
    if (interval) {
      clearInterval(interval);
      set({ pollingInterval: null });
    }
  },
}));
