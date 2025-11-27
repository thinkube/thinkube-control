import { useEffect, useState } from 'react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import api from '@/lib/axios';
import { Activity, Gauge as GaugeIcon } from 'lucide-react';

interface GPUMetrics {
  gpu_utilization: number;
  memory_bandwidth: number;
  system_memory_used_gb: number;
  system_memory_total_gb: number;
  system_memory_percent: number;
  gpu_temp: number;
  power_usage: number;
  cpu_percent: number;
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

  // Calculate color based on percentage
  const getColor = (pct: number) => {
    if (pct < 50) return '#10b981'; // Green
    if (pct < 75) return '#f59e0b'; // Amber
    return '#ef4444'; // Red
  };

  const radius = size / 2 - 10;
  const circumference = Math.PI * radius; // Half circle
  const offset = circumference - (percentage / 100) * circumference;
  const color = getColor(percentage);

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size / 2 + 20 }}>
        <svg width={size} height={size / 2 + 20} className="transform">
          {/* Background arc */}
          <path
            d={`M 10 ${size / 2} A ${radius} ${radius} 0 0 1 ${size - 10} ${size / 2}`}
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth="10"
            strokeLinecap="round"
          />
          {/* Foreground arc */}
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
        {/* Center text */}
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
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = async () => {
    try {
      const response = await api.get('/gpu/metrics');
      setMetrics(response.data);
      setError(null);
    } catch (err: any) {
      console.error('Failed to fetch GPU metrics:', err);
      setError(err.response?.status === 503 ? 'GPU metrics unavailable' : 'Failed to load metrics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();

    // Refresh every 2 seconds
    const interval = setInterval(fetchMetrics, 2000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <TkCard>
        <TkCardHeader>
          <TkCardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            System Metrics
          </TkCardTitle>
        </TkCardHeader>
        <TkCardContent>
          <div className="flex items-center justify-center h-32">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
          </div>
        </TkCardContent>
      </TkCard>
    );
  }

  if (error || !metrics) {
    return (
      <TkCard>
        <TkCardHeader>
          <TkCardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            System Metrics
          </TkCardTitle>
        </TkCardHeader>
        <TkCardContent>
          <div className="text-sm text-muted-foreground text-center py-8">
            {error || 'No metrics available'}
          </div>
        </TkCardContent>
      </TkCard>
    );
  }

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
          {/* System Memory Gauge */}
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

          {/* GPU Utilization Gauge */}
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

        {/* Additional metrics */}
        <div className="grid grid-cols-4 gap-2 mt-4 pt-4 border-t">
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
          <div className="text-center">
            <div className="text-xs text-muted-foreground">Memory BW</div>
            <div className="text-sm font-medium">{metrics.memory_bandwidth.toFixed(1)}%</div>
          </div>
        </div>
      </TkCardContent>
    </TkCard>
  );
}
