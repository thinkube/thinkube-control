/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import type { useTranslation } from 'react-i18next';

export type TFunction = ReturnType<typeof useTranslation>['t'];

/** A model of the gateway's registry, as GET /llm/models/ answers it. */
export interface ModelEntry {
  id: string;
  name: string;
  server_type: string[];
  quantization: string | null;
  size: string | null;
  description: string | null;
  state: string;
  backend_id: string | null;
  tier: string | null;
  is_finetuned: boolean;
  last_error: string | null;
  params_b: number | null;
  active_params_b: number | null;
  context_length: number | null;
  reasoning_format: string | null;
  tool_use: boolean;
  stop_tokens: string[];
  license: string | null;
  gated: boolean;
  speculative_config: string | null;
}

/** A running backend pod, as GET /llm/backends/ answers it. */
export interface BackendEntry {
  id: string;
  name: string;
  url: string;
  type: string;
  status: string;
  models: string[];
}

/** An installed backend type, as GET /llm/backends/types answers it. */
export interface BackendType {
  type: string;
  display_name: string;
  namespace: string;
  driver: string;
  tier: string;
}

export interface GPUAllocation {
  model_id: string;
  backend_id: string;
  node_name: string;
  estimated_memory_gb: number;
  slots: number;
}

export interface GPUMetricEntry {
  index: number;
  utilization: number;
  memory_used_mb: number;
  memory_total_mb: number;
  memory_free_mb: number;
  temp: number;
  power: number;
}

export interface GPUNode {
  name: string;
  gpu_product: string | null;
  gpu_family: string | null;
  gpu_count: number;
  gpu_replicas: number;
  total_slots: number;
  available_slots: number;
  total_memory_gb: number;
  per_gpu_memory_gb: number;
  used_memory_gb: number;
  shared_memory: boolean;
  is_uma: boolean;
  per_gpu_metrics: GPUMetricEntry[];
  allocations: GPUAllocation[];
}

/** GET /llm/gpu/status/ */
export interface GPUStatus {
  nodes: GPUNode[];
  total_memory_gb: number;
  used_memory_gb: number;
  memory_threshold: number;
  can_accept_new_model: boolean;
}

/** GET /llm/models/{id}/load-options */
export interface LoadOptions {
  model_id: string;
  compatible_backends: { id: string; name: string; type: string; status: string; node: string | null }[];
  gpu_nodes: GPUNode[];
  estimated_memory_gb: number;
  context_length: number | null;
}

/** The node's GPU product without the vendor prefix; the node labels may not name one. */
export function formatGpuProduct(t: TFunction, product: string | null): string {
  if (!product) return t('llm.gpu.unknownProduct');
  return product.replace('NVIDIA-', '').replace('NVIDIA ', '').replace(/-/g, ' ');
}

function formatCount(billions: number): string {
  return billions >= 1 ? `${billions}B` : `${(billions * 1000).toFixed(0)}M`;
}

/** Parameters in billions, with the active ones of a mixture of experts. Empty when the catalogue gives none. */
export function formatParams(t: TFunction, params_b: number | null, active_params_b: number | null): string {
  if (!params_b) return '';
  const main = formatCount(params_b);
  if (active_params_b) return t('llm.activeParams', { main, active: formatCount(active_params_b) });
  return main;
}

/** A context length in tokens, shortened to K or M. Empty when the catalogue gives none. */
export function formatContextValue(ctx: number | null): string {
  if (!ctx) return '';
  if (ctx >= 1000000) return `${(ctx / 1000000).toFixed(0)}M`;
  if (ctx >= 1000) return `${Math.round(ctx / 1000)}K`;
  return String(ctx);
}

/** The colour of a memory bar for the share in use. */
export function barColor(pct: number): string {
  if (pct < 50) return 'bg-emerald-500';
  if (pct < 75) return 'bg-amber-500';
  return 'bg-red-500';
}
