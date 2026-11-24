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
