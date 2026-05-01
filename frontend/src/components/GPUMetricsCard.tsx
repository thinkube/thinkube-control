import { useEffect, useState } from 'react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import api from '@/lib/axios';
import { Cpu } from 'lucide-react';

interface GPUMetrics {
  monitoring_available: boolean;
  gpu_utilization: number;
  system_memory_used_gb: number;
  system_memory_total_gb: number;
  system_memory_percent: number;
  gpu_temp: number;
  power_usage: number;
  cpu_percent: number;
  total_gpus: number;
  allocatable_gpus: number;
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
  const [metrics, setMetrics] = useState<GPUMetrics | null>(null);
  const [gpuStatus, setGpuStatus] = useState<GPUStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);

  const fetchMetrics = async () => {
    try {
      const [metricsRes, gpuRes] = await Promise.all([
        api.get('/gpu/metrics'),
        api.get('/llm/gpu/status/'),
      ]);

      if (metricsRes.data.monitoring_available === false) {
        setUnavailable(true);
        setLoading(false);
        return;
      }

      setMetrics(metricsRes.data);
      setGpuStatus(gpuRes.data);
      setUnavailable(false);
    } catch {
      setUnavailable(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, []);

  if (unavailable) return null;
  if (loading) return null;
  if (!metrics) return null;

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle className="flex items-center gap-2">
          <Cpu className="h-5 w-5" />
          System Metrics
        </TkCardTitle>
      </TkCardHeader>
      <TkCardContent>
        {/* Per-node GPU bars */}
        {gpuStatus && gpuStatus.nodes.length > 0 && (
          <div className="space-y-4">
            {gpuStatus.nodes.map(node => (
              <div key={node.name} className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{node.name}</span>
                  <span className="text-xs text-muted-foreground">{formatGpuProduct(node.gpu_product)}</span>
                </div>
                {node.is_uma ? (
                  <GPUBar
                    label="Shared Memory"
                    usedGb={node.used_memory_gb}
                    totalGb={node.total_memory_gb}
                  />
                ) : node.per_gpu_metrics.length > 0 ? (
                  node.per_gpu_metrics.map(gpu => (
                    <GPUBar
                      key={gpu.index}
                      label={`GPU ${gpu.index}`}
                      usedGb={gpu.memory_used_mb / 1024}
                      totalGb={gpu.memory_total_mb / 1024}
                      utilization={gpu.utilization}
                      temp={gpu.temp}
                    />
                  ))
                ) : (
                  <GPUBar
                    label="GPU Memory"
                    usedGb={node.used_memory_gb}
                    totalGb={node.total_memory_gb}
                  />
                )}
              </div>
            ))}
          </div>
        )}

        {/* System stats */}
        <div className="grid grid-cols-3 gap-2 mt-4 pt-4 border-t">
          <div className="text-center">
            <div className="text-xs text-muted-foreground">System RAM</div>
            <div className="text-sm font-medium">
              {metrics.system_memory_used_gb.toFixed(0)} / {metrics.system_memory_total_gb.toFixed(0)} GB
            </div>
          </div>
          <div className="text-center">
            <div className="text-xs text-muted-foreground">Power</div>
            <div className="text-sm font-medium">{metrics.power_usage.toFixed(0)}W</div>
          </div>
          <div className="text-center">
            <div className="text-xs text-muted-foreground">CPU</div>
            <div className="text-sm font-medium">{metrics.cpu_percent.toFixed(1)}%</div>
          </div>
        </div>
      </TkCardContent>
    </TkCard>
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
