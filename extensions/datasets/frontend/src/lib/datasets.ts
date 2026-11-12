/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { AxiosError } from 'axios';
import api from './axios';

export type MappingKind = 'messages' | 'sharegpt' | 'prompt' | 'instruction' | 'text';

export interface Mapping {
  kind: MappingKind;
  columns: Record<string, string>;
}

export interface Split {
  name: string;
  rows: number;
  bytes: number;
}

export interface Column {
  name: string;
  type: string;
}

export type Status = 'importing' | 'identifying' | 'ready' | 'failed';

/** A dataset, described by its latest ready version (`version`). */
export interface Dataset {
  id: string;
  name: string;
  source: 'upload' | 'huggingface';
  origin: string;
  created_by: string;
  created_at: string;
  versions: number;
  latest: number;
  version: number;
  status: Status;
  error: string | null;
  splits: Split[];
  columns: Column[];
  mapping: Mapping | null;
  /** The Argo workflow that makes or made the version */
  workflow: string | null;
}

export interface Recipe {
  id: string;
  name: string;
  document: unknown;
}

/** A version of a dataset, with its lineage: the version it was made from and what made it. */
export interface Version {
  dataset_id: string;
  version: number;
  status: Status;
  error: string | null;
  splits: Split[];
  columns: Column[];
  mapping: Mapping | null;
  rows: number;
  made_from: number | null;
  made_by: 'import' | 'upload' | 'recipe';
  recipe: Recipe | null;
  created_by: string;
  created_at: string;
}

export interface Example {
  example?: { messages?: { role: string; content: string }[]; text?: string };
  error?: string;
}

export interface Rows {
  version: number;
  split: string;
  offset: number;
  rows: Record<string, unknown>[];
  examples: Example[];
}

/** The parts of each kind of mapping: the required ones first. */
export const PARTS: Record<MappingKind, { required: string[]; optional: string[] }> = {
  messages: { required: ['messages'], optional: [] },
  sharegpt: { required: ['conversations'], optional: [] },
  prompt: { required: ['prompt', 'completion'], optional: ['system'] },
  instruction: { required: ['instruction', 'output'], optional: ['input'] },
  text: { required: ['text'], optional: [] },
};

/** The mapping with only the parts of its kind that name a column. */
export function cleanMapping(kind: MappingKind, columns: Record<string, string>): Mapping {
  const { required, optional } = PARTS[kind];
  const kept: Record<string, string> = {};
  for (const part of [...required, ...optional]) {
    if (columns[part]) kept[part] = columns[part];
  }
  return { kind, columns: kept };
}

/** Whether every required part of the kind names a column. */
export function complete(mapping: Mapping): boolean {
  return PARTS[mapping.kind].required.every((part) => !!mapping.columns[part]);
}

export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

/** The message the API gave for a failed call, or the error's own. */
export function failure(e: unknown): string {
  if (e instanceof AxiosError) {
    const detail = e.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail) return JSON.stringify(detail);
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export const datasets = {
  list: () => api.get<Dataset[]>('/datasets').then((r) => r.data),
  get: (id: string, version?: number) =>
    api.get<Dataset>(`/datasets/${id}`, { params: { version } }).then((r) => r.data),
  versions: (id: string) => api.get<Version[]>(`/datasets/${id}/versions`).then((r) => r.data),
  upload: (file: File, name: string) => {
    const form = new FormData();
    form.append('file', file);
    if (name) form.append('name', name);
    return api.post<Dataset>('/datasets/upload', form, { timeout: 600000 }).then((r) => r.data);
  },
  importHub: (repo: string, config: string, splits: string[], name: string) =>
    api
      .post<Dataset>('/datasets/huggingface', {
        repo,
        config: config || null,
        splits: splits.length ? splits : null,
        name: name || null,
      })
      .then((r) => r.data),
  rows: (id: string, version: number, split: string, offset: number) =>
    api.get<Rows>(`/datasets/${id}/rows`, { params: { version, split, offset, limit: 10 } }).then((r) => r.data),
  setMapping: (id: string, version: number, mapping: Mapping) =>
    api.put<Dataset>(`/datasets/${id}/mapping`, mapping, { params: { version } }).then((r) => r.data),
  remove: (id: string) => api.delete(`/datasets/${id}`).then((r) => r.data),
};
