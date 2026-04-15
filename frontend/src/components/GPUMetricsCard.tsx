import { useEffect, useState } from 'react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import api from '@/lib/axios';
import { Gauge as GaugeIcon } from 'lucide-react';

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

// Semicircular gauge component
interface GaugeProps {
  value: number;
  max: number;
  label: string;
  unit: string;
  size?: number;
}

function SemicircularGauge({ value, max, label, unit, size = 140 }: GaugeProps) {
  const percentage = Math.min((value / max) * 100, 100);

  const getColor = (pct: number) => {
    if (pct < 50) return '#10b981';
    if (pct < 75) return '#f59e0b';
    return '#ef4444';
  };

  const radius = size / 2 - 10;
  const circumference = Math.PI * radius;
  const offset = circumference - (percentage / 100) * circumference;
  const color = getColor(percentage);

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size / 2 + 20 }}>
        <svg width={size} height={size / 2 + 20} className="transform">
          <path
            d={`M 10 ${size / 2} A ${radius} ${radius} 0 0 1 ${size - 10} ${size / 2}`}
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth="10"
            strokeLinecap="round"
          />
          <path
            d={`M 10 ${size / 2} A ${radius} ${radius} 0 0 1 ${size - 10} ${size / 2}`}
            fill="none"
            stroke={color}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            className="transition-all duration-500"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center" style={{ top: '10px' }}>
          <div className="text-3xl font-bold" style={{ color }}>{value.toFixed(value >= 10 ? 0 : 1)}{unit}</div>
          <div className="text-xs text-muted-foreground mt-1">{label}</div>
        </div>
      </div>
    </div>
  );
}

export function GPUMetricsCard() {
  const [metrics, setMetrics] = useState<GPUMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);

  const fetchMetrics = async () => {
    try {
      const response = await api.get('/gpu/metrics');
      const data = response.data;

      if (data.monitoring_available === false) {
        setUnavailable(true);
        setLoading(false);
        return;
      }

      setMetrics(data);
      setUnavailable(false);
    } catch {
      // Silently fail — card just won't render
      setUnavailable(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 2000);
    return () => clearInterval(interval);
  }, []);

  // Don't render anything when monitoring is not available
  if (unavailable) return null;
  if (loading) return null;
  if (!metrics) return null;

  return (
    <TkCard>
      <TkCardHeader>
        <TkCardTitle className="flex items-center gap-2">
          <GaugeIcon className="h-5 w-5" />
          System Metrics
        </TkCardTitle>
      </TkCardHeader>
      <TkCardContent>
        <div className="grid grid-cols-2 gap-6">
          <div className="flex flex-col items-center">
            <h3 className="text-sm font-medium mb-3">System Memory</h3>
            <SemicircularGauge
              value={metrics.system_memory_used_gb}
              max={metrics.system_memory_total_gb}
              label={`of ${metrics.system_memory_total_gb.toFixed(0)} GB`}
              unit=" GB"
              size={140}
            />
          </div>

          <div className="flex flex-col items-center">
            <h3 className="text-sm font-medium mb-3">GPU Utilization</h3>
            <SemicircularGauge
              value={metrics.gpu_utilization}
              max={100}
              label="of 100%"
              unit="%"
              size={140}
            />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 mt-4 pt-4 border-t">
          <div className="text-center">
            <div className="text-xs text-muted-foreground">GPU Temp</div>
            <div className="text-sm font-medium">{metrics.gpu_temp}°C</div>
          </div>
          <div className="text-center">
            <div className="text-xs text-muted-foreground">Power</div>
            <div className="text-sm font-medium">{metrics.power_usage.toFixed(1)}W</div>
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
