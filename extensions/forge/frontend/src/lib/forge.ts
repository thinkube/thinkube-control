/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { isAxiosError } from 'axios';
import api from '@/lib/axios';

export interface BaseModel {
  id: string;
  name: string;
  params_b: number | null;
  context_length: number | null;
  description: string | null;
  license: string | null;
}

export interface GpuNode {
  name: string;
  gpu_count: number;
  total_memory_gb: number;
  ai_remaining_gb: number | null;
  loaded_models: string[];
  compute_capability: string | null;
  /** Its GPU computes NVFP4 (Blackwell, compute capability 10.0 and higher). */
  nvfp4: boolean;
}

export interface DatasetSplit {
  name: string;
  rows: number;
  bytes: number;
}

/** A dataset of the Datasets extension that a run can train on, described by its latest ready version. */
export interface Dataset {
  id: string;
  name: string;
  origin: string;
  version: number;
  splits: DatasetSplit[];
  mapping: { kind: string; columns: Record<string, string> };
}

/** A version of a dataset that a run can train on. */
export interface DatasetVersion {
  version: number;
  rows: number;
  splits: DatasetSplit[];
  made_by: 'import' | 'upload' | 'recipe';
  made_from: number | null;
  created_at: string;
  columns?: { name: string; type: string }[];
}

/** What a run read of its dataset. */
export interface RunDataset {
  id: string;
  name: string;
  version: number;
  format: 'messages' | 'text';
  train: { split: string; path: string; rows: number };
  eval: { split: string; path: string; rows: number } | null;
}

export type ChatTarget = 'tuned' | 'base' | 'gguf' | 'nvfp4';

/** A served model the chat panel loads and unloads itself. */
export type ServedTarget = 'base' | 'gguf' | 'nvfp4';

export interface RunExport {
  model_id: string;
  quantization: 'Q8_0' | 'F16';
  node: string;
  job_id: string;
  status: RunStatus;
  error: string | null;
}

/** The run's model quantized to NVFP4, calibrated on rows of its own training split, served by vLLM on Blackwell. */
export interface Nvfp4Export {
  model_id: string;
  calibration_rows: number;
  node: string;
  job_id: string;
  status: RunStatus;
  error: string | null;
}

export interface ChatTurn {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface Preview {
  text: string;
  templated: boolean;
}

export type Method = 'lora' | 'qlora' | 'fp8';

/** A new run: a Meep² job; auto mode decides every decision not named in `decisions`. */
export interface NewRun {
  name: string;
  dataset_id: string;
  dataset_version: number;
  train_split: string;
  eval_split: string | null;
  eval_holdout: number;
  base_model: string;
  method: Method;
  node: string;
  register: boolean;
  steps: number | null;
  epochs: number | null;
  decisions: Record<string, DecisionValue>;
  when: When;
  across_slots: boolean;
  priority: number;
  queue: boolean;
  /** Advanced: the person's choice; null lets Forge size the memory and the node give the cores. */
  memory_gb: number | null;
  cpu_cores: number | null;
  milestones: NewMilestones | null;
}

/** Meep²'s milestones of a new run: every `every` of the planned steps, and the evaluation set they score. */
export interface NewMilestones {
  every: number;
  eval_dataset_id: string | null;
  eval_dataset_version: number | null;
  eval_split: string;
  group_column: string | null;
  source_column: string | null;
  metrics: string[];
}

/** A run's milestones as Forge keeps them. */
export interface RunMilestones {
  every: number;
  metrics: string[];
  eval: {
    id: string;
    name: string;
    version: number;
    split: string;
    rows: number;
    group_column: string | null;
    source_column: string | null;
  } | null;
}

/** A milestone Meep² made: its step, its folder and, with an evaluation, each group's scores. */
export interface MilestoneRecord {
  step: number;
  planned_steps: number;
  folder: string;
  seconds: number;
  rows?: number;
  scores?: Record<string, Record<string, number>>;
}

export type When = 'now' | 'slots';

export type DecisionValue = string | number | boolean | string[];

/** One decision of Meep²'s plan: what, why, and whether it is auto mode's or the person's. */
export interface Decision {
  name: string;
  label: string;
  value: DecisionValue | null;
  text: string;
  reason: string;
  by: 'auto' | 'user';
  auto_value: DecisionValue | null;
  auto_text: string;
}

export interface Plan {
  model: string;
  task: string;
  method: string;
  device: string;
  free_bytes: number | null;
  decisions: Decision[];
  seconds_per_step?: number | null;
  estimated_seconds?: number | null;
  /** The training's peaks, and the memory its job asks for (the peak and Meep²'s margin); null where Meep² cannot size it. */
  peak_gpu_bytes?: number | null;
  peak_host_bytes?: number | null;
  memory_request_bytes?: number | null;
}

/** Meep²'s status file, as the training last wrote it. */
export interface TrainingStatus {
  state: 'planning' | 'training' | 'stopping' | 'paused' | 'completed' | 'failed';
  step: number | null;
  total_steps: number | null;
  loss: number | null;
  seconds_per_step: number | null;
  eta_seconds: number | null;
  checkpoint: string | null;
  plan: Plan | null;
  error: string | null;
  updated: string | null;
  eval_loss?: number | null;
  eval_step?: number | null;
  milestones?: MilestoneRecord[] | null;
  milestone_seconds?: number | null;
  next_milestone?: number | null;
}

/** A run's place in the training queue. */
export type RunState =
  | 'preprocessing'
  | 'planning'
  | 'planned'
  | 'queued'
  | 'running'
  | 'stopping'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

export const RUN_STATES_OPEN: RunState[] = ['preprocessing', 'planning', 'queued', 'running', 'stopping', 'paused'];

/** The preprocessing of a run's data by Meep² before its plan: tokenized, checked and cut once, into a folder keyed by
 * what made it; the key, folder, found and report come once its job has completed. */
export interface RunPreprocess {
  node: string;
  job_id: string;
  key?: string;
  folder?: string;
  /** Made before from the same inputs, and found; else built for this run. */
  found?: boolean;
  report?: {
    decision: { text: string; reason: string; value: Record<string, unknown> };
    rows: number;
    trained_rows: number;
    eval_rows: number | null;
    held: number;
  };
}

/** The decisions of a plan a person may set: fields of Meep²'s Recipe and Overrides. */
export const SETTABLE_DECISIONS = [
  'rank', 'alpha', 'dropout', 'learning_rate', 'schedule', 'warmup_steps', 'tokens_per_update', 'effective_batch',
  'optimizer', 'max_length', 'max_grad_norm', 'weight_decay', 'experts', 'answers_only', 'attention', 'liger',
  'padding_free', 'fused_lora', 'compile_layers', 'checkpointed_layers', 'selective_layers', 'micro_batch', 'milestone_engine',
  'eval_every',
];

/** The job field a person sets for a decision of the plan: its own name, but eval_every for "evaluation". */
export function decisionField(name: string): string {
  return name === 'evaluation' ? 'eval_every' : name;
}

export interface SlotWindow {
  node: string;
  start: string;
  end: string;
  slot_ids: string[];
}

export interface Slot {
  id: string;
  node: string;
  days: number[];
  start: string;
  end: string;
  timezone: string;
  next_window: SlotWindow | null;
  overlaps: { node: string; start: string; end: string }[];
}

export interface NewSlot {
  node: string;
  days: number[];
  start: string;
  end: string;
  timezone: string;
}

export interface WorkerStatus {
  connected: boolean;
  username?: string | null;
  connected_at?: string | null;
  last_tick: string | null;
  error: string | null;
}

/** A kind of work on a GPU node, and the priority Forge gives it. */
export interface KindPriority {
  kind: 'notebook_server' | 'notebook_job' | 'forge_run' | 'served_model' | 'app';
  label: string;
  default: number;
  priority: number;
  stopped_by: string;
}

/** 1 is the highest priority, 5 the lowest. */
export const PRIORITIES = [1, 2, 3, 4, 5];

export interface QueueNode {
  node: string;
  slot: SlotWindow | null;
  next_slot: SlotWindow | null;
  training_mode: boolean;
  note: string | null;
  runs: Run[];
}

/** The state of a notebook job of Thinkube Control. */
export type RunStatus = 'starting' | 'running' | 'completed' | 'error' | 'cancelled' | 'lost';

/** One output of a notebook cell, as Jupyter gives it. */
export interface CellOutput {
  output_type: 'stream' | 'execute_result' | 'display_data' | 'error' | string;
  name?: string;
  text?: string;
  data?: Record<string, string>;
  ename?: string;
  evalue?: string;
  traceback?: string[];
}

export interface CellResult {
  success: boolean;
  cell_index: number;
  outputs: CellOutput[];
}

export interface Job {
  status: RunStatus;
  total_cells: number | null;
  completed_cells: number | null;
  failed_cell_index: number | null;
  error: string | null;
  results: CellResult[] | null;
}

/** The text a cell printed, with an error's name and message last. */
export function cellText(result: CellResult): string {
  return result.outputs
    .map((o) => {
      if (o.output_type === 'error') return `${o.ename}: ${o.evalue}`;
      if (o.text) return o.text;
      return o.data?.['text/plain'] ?? '';
    })
    .join('');
}

export interface Run {
  id: string;
  name: string;
  dataset_id: string;
  dataset_version: number;
  dataset: RunDataset | null;
  evaluation: { split?: string; holdout?: number } | null;
  milestones: RunMilestones | null;
  base_model: string;
  method: Method;
  node: string;
  register: boolean;
  job_spec: Record<string, unknown> | null;
  preprocess: RunPreprocess | null;
  plan: Plan | null;
  plan_job_id: string | null;
  progress: TrainingStatus | null;
  when: When;
  across_slots: boolean;
  position: number | null;
  queue_on_plan: boolean;
  priority: number;
  paused_by: 'slot' | 'user' | 'preempted' | null;
  preempted_by: string | null;
  waiting: string | null;
  stop_requested_at: string | null;
  memory_gb: number | null;
  cpu_cores: number | null;
  /** The memory the training job asks for: the person's, or Meep²'s plan's; null while the plan does not size it. */
  training_memory_gb: number | null;
  /** Meep²'s memory line: what it asks for, and why. */
  memory_reason: string | null;
  eta_seconds: number | null;
  export: RunExport | null;
  nvfp4: Nvfp4Export | null;
  model_id: string | null;
  job_id: string | null;
  status: RunState;
  error: string | null;
  mlflow_run_id: string | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  job?: Job | null;
}

export interface Point {
  step: number;
  value: number;
}

export interface Progress {
  mlflow_run_id: string;
  status: string;
  url: string;
  metrics: Record<'loss' | 'eval_loss' | 'learning_rate' | 'grad_norm' | 'epoch', Point[]>;
}

/** A model's entry in the LLM Gateway, as Control's model status gives it. */
export interface ModelStatus {
  model: {
    id: string;
    state: 'registered' | 'deployable' | 'loading' | 'available' | 'unloading' | string;
    backend_id: string | null;
    last_error: string | null;
  };
  backends: string[];
}

/** What Control answers to a load or an unload. */
export interface ModelChange {
  model_id: string;
  state: string;
  message: string;
}

/** A notebook job that has not ended. */
export const OPEN_STATES: RunStatus[] = ['starting', 'running'];

/** A duration in seconds as hours and minutes, such as "3 h 05 min". */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '–';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
}

/** A plan's value of a decision as a person types it: a number, true or false, or a word. */
export function parseDecision(text: string, like: DecisionValue | null): DecisionValue {
  const trimmed = text.trim();
  if (typeof like === 'boolean' || trimmed === 'true' || trimmed === 'false') return trimmed === 'true';
  if (trimmed !== '' && !Number.isNaN(Number(trimmed))) return Number(trimmed);
  return trimmed;
}

/** The message the API gave for a failed call, or the error's own. Inside Thinkube Control the call
 * fails with an error of Control's own axios, which isAxiosError knows by its flag, not by its class. */
export function failure(e: unknown): string {
  if (isAxiosError(e)) {
    const detail = e.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail) return JSON.stringify(detail);
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export const forge = {
  models: () => api.get<BaseModel[]>('/models').then((r) => r.data),
  nodes: () => api.get<GpuNode[]>('/nodes').then((r) => r.data),
  datasets: () => api.get<Dataset[]>('/datasets').then((r) => r.data),
  datasetVersions: (datasetId: string) =>
    api.get<DatasetVersion[]>(`/datasets/${datasetId}/versions`).then((r) => r.data),
  preview: (datasetId: string, version: number, model: string, split: string) =>
    api.get<Preview>(`/datasets/${datasetId}/preview`, { params: { model, version, split } }).then((r) => r.data),
  runs: () => api.get<Run[]>('/runs').then((r) => r.data),
  run: (id: string) => api.get<Run>(`/runs/${id}`).then((r) => r.data),
  createRun: (run: NewRun) => api.post<Run>('/runs', run, { timeout: 120000 }).then((r) => r.data),
  replan: (id: string, decisions: Record<string, DecisionValue>) =>
    api.post<Run>(`/runs/${id}/plan`, { decisions }).then((r) => r.data),
  queueRun: (id: string, when?: When, across_slots?: boolean) =>
    api.post<Run>(`/runs/${id}/queue`, { when, across_slots }).then((r) => r.data),
  setPriority: (id: string, priority: number) => api.post<Run>(`/runs/${id}/priority`, { priority }).then((r) => r.data),
  priorities: () => api.get<KindPriority[]>('/priorities').then((r) => r.data),
  setPriorities: (changes: Record<string, number>) => api.put<KindPriority[]>('/priorities', changes).then((r) => r.data),
  setResources: (id: string, memory_gb: number | null, cpu_cores: number | null) =>
    api.post<Run>(`/runs/${id}/resources`, { memory_gb, cpu_cores }).then((r) => r.data),
  pauseRun: (id: string) => api.post<Run>(`/runs/${id}/pause`).then((r) => r.data),
  resumeRun: (id: string) => api.post<Run>(`/runs/${id}/resume`).then((r) => r.data),
  cancelRun: (id: string) => api.post<Run>(`/runs/${id}/cancel`).then((r) => r.data),
  queue: () => api.get<{ nodes: QueueNode[]; worker: WorkerStatus }>('/queue').then((r) => r.data),
  reorder: (node: string, runs: string[]) => api.put<Run[]>(`/queue/${node}/order`, { runs }).then((r) => r.data),
  slots: () => api.get<Slot[]>('/slots').then((r) => r.data),
  createSlot: (slot: NewSlot) => api.post<Slot>('/slots', slot).then((r) => r.data),
  updateSlot: (id: string, slot: NewSlot) => api.put<Slot>(`/slots/${id}`, slot).then((r) => r.data),
  deleteSlot: (id: string) => api.delete(`/slots/${id}`).then((r) => r.data),
  worker: () => api.get<WorkerStatus>('/worker').then((r) => r.data),
  connectWorker: () => api.post<WorkerStatus>('/worker/connect').then((r) => r.data),
  disconnectWorker: () => api.delete<WorkerStatus>('/worker/connect').then((r) => r.data),
  progress: (id: string) => api.get<Progress | null>(`/runs/${id}/progress`).then((r) => r.data),
  model: (id: string) => api.get<ModelStatus>(`/runs/${id}/model`).then((r) => r.data),
  loadModel: (id: string, node: string | null) =>
    api.post<ModelChange>(`/runs/${id}/model/load`, { node }).then((r) => r.data),
  unloadModel: (id: string) => api.post<ModelChange>(`/runs/${id}/model/unload`).then((r) => r.data),
  servedModel: (id: string, target: ServedTarget) => api.get<ModelStatus>(`/runs/${id}/${target}`).then((r) => r.data),
  loadServed: (id: string, target: ServedTarget) =>
    api.post<ModelChange>(`/runs/${id}/${target}/load`, { node: null }).then((r) => r.data),
  unloadServed: (id: string, target: ServedTarget) =>
    api.post<ModelChange>(`/runs/${id}/${target}/unload`).then((r) => r.data),
  exportNvfp4: (id: string, node: string, calibration_rows: number) =>
    api.post<Run>(`/runs/${id}/export/nvfp4`, { node, calibration_rows }, { timeout: 120000 }).then((r) => r.data),
  exportRun: (id: string, quantization: 'Q8_0' | 'F16', node: string) =>
    api.post<Run>(`/runs/${id}/export`, { quantization, node }).then((r) => r.data),
  chat: (id: string, target: ChatTarget, messages: ChatTurn[]) =>
    api
      .post<{ content: string }>(`/runs/${id}/chat`, { target, messages }, { timeout: 300000 })
      .then((r) => r.data),
};
