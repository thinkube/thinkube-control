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

export interface DownloadStatus {
  workflow_name: string;
  model_id: string | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  message: string;
  is_running: boolean;
  is_complete: boolean;
  is_failed: boolean;
}

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
      const response = await api.get('/api/models/catalog');
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

      const response = await api.post('/api/models/download', payload);

      const model = get().models.find(m => m.id === modelId);
      const modelName = model?.name || modelId;

      toast.success(`Download started: ${modelName}`);

      // Immediately fetch downloads to update UI
      await get().fetchDownloads();

      // Start polling if not already running
      if (!get().pollingInterval) {
        get().startPolling();
      }

      set({ loading: false });
    } catch (error: any) {
      const errorMsg = error.response?.data?.detail || 'Failed to start download';
      set({ error: errorMsg, loading: false });
      toast.error(errorMsg);
    }
  },

  fetchDownloads: async () => {
    try {
      const response = await api.get('/api/models/downloads');
      const previousDownloads = get().downloads;
      const newDownloads: DownloadStatus[] = response.data.downloads;

      // Check for completed downloads to show notifications
      previousDownloads.forEach(prevDl => {
        const newDl = newDownloads.find(d => d.workflow_name === prevDl.workflow_name);

        // If download just completed
        if (prevDl.is_running && newDl && newDl.is_complete) {
          const model = get().models.find(m => m.id === newDl.model_id);
          const modelName = model?.name || newDl.model_id || 'Model';
          toast.success(`✓ ${modelName} downloaded successfully!`);
        }

        // If download just failed
        if (prevDl.is_running && newDl && newDl.is_failed) {
          const model = get().models.find(m => m.id === newDl.model_id);
          const modelName = model?.name || newDl.model_id || 'Model';
          toast.error(`✗ ${modelName} download failed`);
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
      await api.delete(`/api/models/downloads/${workflowId}`);
      toast.success('Download cancelled');
      await get().fetchDownloads();
    } catch (error: any) {
      const errorMsg = error.response?.data?.detail || 'Failed to cancel download';
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
