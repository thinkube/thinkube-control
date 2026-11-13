/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { create } from 'zustand';
import { toast } from 'sonner';
import api from '@/lib/axios';
import i18n from '@/i18n';

export interface Model {
  id: string;
  name: string;
  size: string | null;
  quantization: string;
  description: string;
  server_type: string[];
  is_downloaded: boolean;
  is_finetuned: boolean;
  task: string;
  params_b: number | null;
  active_params_b: number | null;
  context_length: number | null;
  reasoning_format: string | null;
  tool_use: boolean;
  license: string | null;
  gated: boolean;
}

export interface MirrorJob {
  id: string;
  model_id: string;
  status: string;
  workflow_name: string | null;
  /** The workflow's page in Argo's web UI, from the backend */
  workflow_url: string | null;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
  is_running: boolean;
  is_complete: boolean;
  is_failed: boolean;
}

/** How often the mirror jobs are read again while one of them runs. */
export const POLL_INTERVAL_MS = 10000;

/** The reason a failed call gives: the API's detail, else the client's own message. */
export function errorText(err: unknown): string {
  const failure = err as { response?: { data?: { detail?: unknown } }; message?: string } | null;
  const detail = failure?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail) return JSON.stringify(detail);
  return failure?.message || String(err);
}

interface ModelsState {
  models: Model[];
  mirrors: MirrorJob[];
  loading: boolean;
  error: string | null;
  /** Why the last reading of the mirror jobs failed; cleared by the next one that succeeds. */
  mirrorsError: string | null;
  pollingInterval: ReturnType<typeof setInterval> | null;

  getMirrorForModel: (modelId: string) => MirrorJob | undefined;
  isModelMirroring: (modelId: string) => boolean;

  fetchModels: () => Promise<void>;
  startMirror: (modelId: string) => Promise<void>;
  fetchMirrors: () => Promise<void>;
  cancelMirror: (workflowId: string) => Promise<void>;
  resetMirrorJob: (modelId: string) => Promise<void>;
  deleteModel: (modelId: string) => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

const modelName = (models: Model[], modelId: string) => models.find((m) => m.id === modelId)?.name || modelId;

export const initialModelsState = {
  models: [] as Model[],
  mirrors: [] as MirrorJob[],
  loading: false,
  error: null as string | null,
  mirrorsError: null as string | null,
  pollingInterval: null as ReturnType<typeof setInterval> | null,
};

/** The model catalogue and the mirror jobs of the extension's API, read while the Models page is open. */
export const useModelsStore = create<ModelsState>((set, get) => ({
  ...initialModelsState,

  getMirrorForModel: (modelId: string) => get().mirrors.find((job) => job.model_id === modelId),

  isModelMirroring: (modelId: string) => get().getMirrorForModel(modelId)?.is_running ?? false,

  fetchModels: async () => {
    set({ loading: true, error: null });
    try {
      const { data } = await api.get('/models/catalog');
      set({ models: data.models, loading: false });
    } catch (err) {
      const message = errorText(err);
      set({ error: message, loading: false });
      toast.error(message);
    }
  },

  startMirror: async (modelId: string) => {
    set({ loading: true, error: null });
    try {
      await api.post('/models/mirrors', { model_id: modelId });
      toast.success(i18n.t('models.toast.mirrorStarted', { name: modelName(get().models, modelId) }));
      await get().fetchMirrors();
      get().startPolling();
      set({ loading: false });
    } catch (err) {
      const message = errorText(err);
      set({ error: message, loading: false });
      toast.error(message);
    }
  },

  fetchMirrors: async () => {
    try {
      const { data } = await api.get('/models/mirrors');
      const previous = get().mirrors;
      const jobs: MirrorJob[] = data.jobs;

      // A job that was running at the last reading and has ended since is announced once
      previous.forEach((before) => {
        const now = jobs.find((job) => job.workflow_name === before.workflow_name);
        if (!before.is_running || !now) return;
        const name = modelName(get().models, now.model_id);
        if (now.is_complete) toast.success(i18n.t('models.toast.mirrored', { name }));
        if (now.is_failed) toast.error(i18n.t('models.toast.mirrorFailed', { name }));
      });

      set({ mirrors: jobs, mirrorsError: null });

      // A mirror that has ended changes the catalogue: the model is in MLflow now
      if (previous.some((before) => before.is_running) && jobs.every((job) => !job.is_running)) {
        get().fetchModels();
      }

      if (!jobs.some((job) => job.is_running)) {
        get().stopPolling();
      }
    } catch (err) {
      set({ mirrorsError: errorText(err) });
    }
  },

  cancelMirror: async (workflowId: string) => {
    try {
      await api.delete(`/models/mirrors/${workflowId}`);
      toast.success(i18n.t('models.toast.cancelled'));
      await get().fetchMirrors();
    } catch (err) {
      toast.error(errorText(err));
    }
  },

  resetMirrorJob: async (modelId: string) => {
    try {
      // The model's id holds a slash, which the API's path takes as it is
      await api.post(`/models/mirrors/reset/${modelId}`);
      toast.success(i18n.t('models.toast.reset'));
      await Promise.all([get().fetchModels(), get().fetchMirrors()]);
    } catch (err) {
      toast.error(errorText(err));
    }
  },

  deleteModel: async (modelId: string) => {
    try {
      const name = modelName(get().models, modelId);
      await api.delete(`/models/registered/${modelId}`);
      toast.success(i18n.t('models.toast.deleted', { name }));
      await Promise.all([get().fetchModels(), get().fetchMirrors()]);
    } catch (err) {
      toast.error(errorText(err));
    }
  },

  startPolling: () => {
    if (get().pollingInterval) return;
    const interval = setInterval(() => {
      get().fetchMirrors();
    }, POLL_INTERVAL_MS);
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
