/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from 'react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import api from '@/lib/axios';
import { Cpu } from 'lucide-react';

interface NodeStats {
  name: string;
  memory_used_gb: number | null;
  memory_total_gb: number;
  cpu_percent: number | null;
  cpu_cores: number | null;
  gpu_power_watts: number | null;
}

interface NodeMetrics {
  monitoring_available: boolean;
  nodes?: NodeStats[];
  timestamp: string;
}

interface GPUMetricEntry {
  index: number;
  utilization: number;
  memory_used_mb: number;
  memory_total_mb: number;
  memory_free_mb: number;
  temp: number;
  power: number;
}

interface GPUNode {
  name: string;
  gpu_product: string | null;
  gpu_count: number;
  total_memory_gb: number;
  used_memory_gb: number;
  is_uma: boolean;
  per_gpu_metrics: GPUMetricEntry[];
}

interface GPUStatus {
  nodes: GPUNode[];
}

function getBarColor(pct: number): string {
  if (pct < 50) return 'bg-emerald-500';
  if (pct < 75) return 'bg-amber-500';
  return 'bg-red-500';
}

function formatGpuProduct(product: string | null): string {
  if (!product) return 'GPU';
  return product.replace('NVIDIA-', '').replace('NVIDIA ', '').replace(/-/g, ' ');
}

export function GPUMetricsCard() {
  const [metrics, setMetrics] = useState<NodeMetrics | null>(null);
  const [gpuStatus, setGpuStatus] = useState<GPUStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = async () => {
    try {
      const [metricsRes, gpuRes] = await Promise.all([
        api.get<NodeMetrics>('/gpu/metrics'),
        api.get<GPUStatus>('/llm/gpu/status/'),
      ]);
      setMetrics(metricsRes.data);
      setGpuStatus(gpuRes.data);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail ?? err.message);
    }
  };

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, []);

  if (!metrics && !error) return null;
  if (metrics && !metrics.monitoring_available) return null;

  const gpuNodes = new Map((gpuStatus?.nodes ?? []).map(n => [n.name, n]));

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle className="flex items-center gap-2">
          <Cpu className="h-5 w-5" />
          Nodes
        </TkCardTitle>
      </TkCardHeader>
      <TkCardContent>
        {error && (
          <p className="text-sm text-destructive mb-4">Metrics could not be read: {error}</p>
        )}
        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          {(metrics?.nodes ?? []).map(stats => (
            <NodeSection key={stats.name} stats={stats} gpuNode={gpuNodes.get(stats.name)} />
          ))}
        </div>
      </TkCardContent>
    </TkCard>
  );
}

function NodeSection({ stats, gpuNode }: { stats: NodeStats; gpuNode?: GPUNode }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{stats.name}</span>
        {gpuNode && (
          <span className="text-xs text-muted-foreground">{formatGpuProduct(gpuNode.gpu_product)}</span>
        )}
      </div>

      {gpuNode && (gpuNode.is_uma ? (
        <GPUBar
          label="Shared memory (GPU and system)"
          usedGb={gpuNode.used_memory_gb}
          totalGb={gpuNode.total_memory_gb}
        />
      ) : gpuNode.per_gpu_metrics.length > 0 ? (
        gpuNode.per_gpu_metrics.map(gpu => (
          <GPUBar
            key={gpu.index}
            label={`GPU ${gpu.index} memory`}
            usedGb={gpu.memory_used_mb / 1024}
            totalGb={gpu.memory_total_mb / 1024}
            utilization={gpu.utilization}
            temp={gpu.temp}
          />
        ))
      ) : (
        <GPUBar
          label="GPU memory"
          usedGb={gpuNode.used_memory_gb}
          totalGb={gpuNode.total_memory_gb}
        />
      ))}

      {!gpuNode?.is_uma && (
        stats.memory_used_gb != null ? (
          <GPUBar
            label="System memory"
            usedGb={stats.memory_used_gb}
            totalGb={stats.memory_total_gb}
          />
        ) : (
          <StatLine label="System memory" value="not reported" />
        )
      )}

      <StatLine
        label="CPU"
        value={
          stats.cpu_percent != null
            ? `${stats.cpu_percent.toFixed(0)}%${stats.cpu_cores != null ? ` of ${stats.cpu_cores} cores` : ''}`
            : 'not reported'
        }
      />
      {gpuNode && (
        <StatLine
          label={gpuNode.gpu_count > 1 ? `GPU power (${gpuNode.gpu_count} GPUs)` : 'GPU power'}
          value={stats.gpu_power_watts != null ? `${stats.gpu_power_watts.toFixed(0)} W` : 'not reported'}
        />
      )}
    </div>
  );
}

function StatLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-xs text-muted-foreground">
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

function GPUBar({
  label,
  usedGb,
  totalGb,
  utilization,
  temp,
}: {
  label: string;
  usedGb: number;
  totalGb: number;
  utilization?: number;
  temp?: number;
}) {
  const pct = totalGb > 0 ? (usedGb / totalGb) * 100 : 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span>
          {usedGb.toFixed(1)} / {totalGb.toFixed(0)} GB
          {utilization != null && utilization > 0 && ` · ${utilization.toFixed(0)}%`}
          {temp != null && temp > 0 && ` · ${temp}°C`}
        </span>
      </div>
      <div className="h-2.5 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${getBarColor(pct)}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}
