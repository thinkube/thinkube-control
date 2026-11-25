/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

/** What the scheduler's backend answers about the nodes, and how the pages word it. */

/** What the work on a node shares: each GPU's own memory, the node's one memory, or no GPU at all. */
export type NodeKind = 'separate' | 'shared' | 'none';

export interface Gpu {
  /** The GPU's id: what a pod is given to run on this GPU. */
  id: string;
  index: number;
  name: string;
  utilization: number;
  temperature: number;
  power: number;
  /** Of a GPU with memory of its own; a GPU that shares the system's memory has none of these. */
  memory_total_bytes?: number;
  memory_used_bytes?: number;
  memory_free_bytes?: number;
}

export interface SchedulerNode {
  name: string;
  /** Why the node could not be read; its figures are then missing. */
  error?: string;
  kind?: NodeKind;
  gpus?: Gpu[];
  memory_total_bytes?: number;
  memory_available_bytes?: number;
}

export const KIND_TEXT: Record<NodeKind, string> = {
  separate: 'Each GPU has memory of its own: work shares only the GPU it is on',
  shared: "The GPU takes its memory from the system's: all the work on the node shares one memory",
  none: 'No GPU',
};

export type JobStatus = 'waiting' | 'running' | 'stopping' | 'completed' | 'failed' | 'stopped';

/** A job: a command the scheduler runs in a pod on a node, on the GPUs it chooses. */
export interface Job {
  id: string;
  name: string;
  /** The service the job is of, such as forge or mill. */
  owner: string;
  node: string;
  command: string[];
  gpus: number;
  /** The memory it takes on each GPU; null when it has its GPUs alone. */
  gpu_memory_gb: number | null;
  memory_gb: number;
  cpu_cores: number;
  priority: number;
  status: JobStatus;
  /** The ids of the GPUs it holds. */
  gpu_ids: string[] | null;
  starts: number;
  waiting: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** What a job asks of its node's GPUs, in words. */
export function gpuNeed(job: Job): string {
  if (!job.gpus) return 'no GPU';
  const count = job.gpus === 1 ? '1 GPU' : `${job.gpus} GPUs`;
  return job.gpu_memory_gb === null ? `${count}, alone` : `${count}, ${job.gpu_memory_gb} GiB on each`;
}

/** The reason an API call failed, in the backend's own words when it gave them. */
export function failureText(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return e?.message || String(err);
}

/** Bytes as GiB with one decimal, such as "22.1 GiB". */
export function gib(bytes: number): string {
  return `${(bytes / 2 ** 30).toFixed(1)} GiB`;
}
