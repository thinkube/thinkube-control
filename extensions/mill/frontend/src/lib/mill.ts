/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

/** What the Mill backend answers about runs, and how the pages word it. */

export type RunState = 'running' | 'completed' | 'failed' | 'stopped';

export interface Stage {
  name: string;
  total: number | null;
  done: number;
  started_at: string;
  ended_at: string | null;
}

export interface RunSummary {
  id: string;
  task: string;
  file: string;
  limit: number | null;
  host: string | null;
  node: string | null;
  runner: string | null;
  commit: string | null;
  dirty: boolean | null;
  // Names and values attached to the run, such as the experiment it belongs to
  tags?: Record<string, string>;
  state: RunState;
  error: string | null;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
  stages: Stage[];
  samples: number;
  failures: number;
}

export interface Measure {
  count: number;
  sum: number;
  min: number;
  max: number;
}

export interface RunStatus {
  state: RunState;
  error: string | null;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
  stages: Stage[];
  counters: Record<string, number>;
  measures: Record<string, Measure>;
  samples: number;
  failures: number;
}

export interface Port {
  type: string;
  path: string;
}

export interface RunInfo {
  id: string;
  task: string;
  description?: string;
  file: string;
  repository: { root: string; commit: string | null; dirty: boolean } | null;
  inputs?: Record<string, Port>;
  outputs?: Record<string, Port>;
  settings?: Record<string, unknown>;
  limit?: number | null;
  host?: string;
  node?: string;
  runner?: string;
}

export interface RunDetail {
  run: RunInfo;
  status: RunStatus;
  // Names and values attached to the run, such as the experiment it belongs to
  tags?: Record<string, string>;
  can_stop: boolean;
  has_log: boolean;
}

export interface Sample {
  n: number;
  at: string;
  fields: Record<string, unknown>;
}

export interface Failure {
  n: number;
  at: string;
  error: string;
  fields: Record<string, unknown>;
}

/** The reason an API call failed, in the backend's own words when it gave them. */
export function failureText(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return e?.message || String(err);
}

/** How far a stage is, 0 to 100; null when its total is not known. */
export function percent(stage: Stage): number | null {
  if (stage.total === null) return null;
  if (stage.total === 0) return 100;
  return Math.min(100, (stage.done / stage.total) * 100);
}

/** A length of time as "1h 02m", "3m 05s" or "12s". */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, '0');
  if (h > 0) return `${h}h ${pad(m)}m`;
  if (m > 0) return `${m}m ${pad(s % 60)}s`;
  return `${s}s`;
}

/** The seconds a stage has left at the pace it has had; null when that cannot be said. */
export function secondsLeft(stage: Stage, now: number): number | null {
  if (stage.ended_at !== null || stage.total === null || stage.done <= 0) return null;
  const elapsed = (now - new Date(stage.started_at).getTime()) / 1000;
  if (elapsed <= 0) return null;
  return ((stage.total - stage.done) * elapsed) / stage.done;
}

/** A value in a table cell: a string as it is, nothing for a missing value, anything else as compact JSON. */
export function cellText(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}
