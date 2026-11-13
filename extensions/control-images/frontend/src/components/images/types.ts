/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

/** An image of the list, as GET /images answers it. */
export interface MirroredImage {
  id: string;
  name: string;
  registry: string;
  repository: string;
  tag: string;
  source_url: string | null;
  destination_url: string;
  description: string | null;
  category: 'system' | 'user';
  protected: boolean;
  is_base: boolean;
  template: string | null;
  mirror_date: string | null;
  last_synced: string | null;
  metadata: Record<string, unknown>;
  digest: string | null;
  size_bytes: string | null;
  vulnerabilities: Record<string, number>;
}

/** The counts GET /images/stats answers. */
export interface ImageStats {
  total: number;
  by_category: { system: number; user: number };
  protected: number;
}

/** A mirror job, as POST /images, POST /images/{id}/remirror and GET /jobs/{id} answer it. */
export interface MirrorJob {
  id: string;
  image_id: string | null;
  job_type: string;
  status: string;
  source_url: string;
  error_message: string | null;
  logs: string | null;
  workflow: string | null;
}

/** What starting a mirror answers. */
export interface MirrorStarted {
  image: MirroredImage;
  job: MirrorJob;
}

/** A custom image, as GET /custom-images answers it. */
export interface CustomImage {
  id: string;
  name: string;
  dockerfile_path: string;
  status: string;
  build_config: { description?: string; base_image?: string } | null;
  workflow: string | null;
  registry_url: string | null;
  is_base: boolean;
  scope: string;
  parent_image_id: string | null;
  template: string | null;
  created_at: string;
}

/** The reason an API call failed: the detail the backend gave, else the client's message. */
export function errorText(err: unknown): string {
  const failure = err as { response?: { data?: { detail?: unknown } }; message?: string } | null;
  const detail = failure?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail) return JSON.stringify(detail);
  return failure?.message || String(err);
}

/** A timestamp of the API in the reader's locale; `empty` when there is none. */
export function formatDate(value: string | null | undefined, empty = '-'): string {
  if (!value) return empty;
  const date = new Date(value);
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
}

/** Opens an address of Thinkube IDE in a new tab. */
export function openInNewTab(url: string): void {
  window.open(url, '_blank', 'noopener');
}

/** How often a running mirror or build is read again. */
export const POLL_INTERVAL_MS = 3000;

/** The badge colour of a mirror job's or a build's status. */
export function statusBadge(status: string): 'healthy' | 'unhealthy' | 'warning' | 'pending' {
  if (status === 'success') return 'healthy';
  if (status === 'failed') return 'unhealthy';
  if (status === 'running' || status === 'building') return 'warning';
  return 'pending';
}
