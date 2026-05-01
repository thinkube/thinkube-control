import { useState, useEffect } from 'react';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkLabel } from 'thinkube-style/components/forms-inputs';
import {
  TkSelect,
  TkSelectTrigger,
  TkSelectValue,
  TkSelectContent,
  TkSelectItem,
} from 'thinkube-style/components/forms-inputs';
import { Loader2, Zap } from 'lucide-react';
import api from '../lib/axios';

interface GPUAllocation {
  model_id: string;
  backend_id: string;
  node_name: string;
  estimated_memory_gb: number;
  slots: number;
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

interface LoadOptions {
  model_id: string;
  compatible_backends: { id: string; name: string; type: string; status: string; node: string | null }[];
  gpu_nodes: GPUNode[];
  estimated_memory_gb: number;
  context_length: number | null;
}

const BACKEND_TYPE_LABELS: Record<string, string> = {
  ollama: 'Ollama',
  vllm: 'vLLM',
  'tensorrt-llm': 'TensorRT-LLM',
};

const CONTEXT_OPTIONS = [
  { value: 2048, label: '2K' },
  { value: 4096, label: '4K' },
  { value: 8192, label: '8K' },
  { value: 16384, label: '16K' },
  { value: 32768, label: '32K' },
  { value: 65536, label: '64K' },
  { value: 131072, label: '128K' },
  { value: 262144, label: '256K' },
  { value: 524288, label: '512K' },
];

const DEFAULT_CONTEXT = 8192;
const LARGE_CONTEXT_THRESHOLD = 32768;

interface Props {
  modelId: string;
  modelName: string;
  serverType: string[];
  size: string | null;
  quantization: string | null;
  params_b: number | null;
  active_params_b: number | null;
  context_length: number | null;
  reasoning_format: string | null;
  tool_use: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onLoaded: () => void;
}

function formatGpuProduct(product: string | null): string {
  if (!product) return 'GPU';
  return product
    .replace('NVIDIA-', '')
    .replace('NVIDIA ', '')
    .replace('-', ' ');
}

function formatParams(params_b: number | null, active_params_b: number | null): string {
  if (!params_b) return '';
  const main = params_b >= 1 ? `${params_b}B` : `${(params_b * 1000).toFixed(0)}M`;
  if (active_params_b) {
    const active = active_params_b >= 1 ? `${active_params_b}B` : `${(active_params_b * 1000).toFixed(0)}M`;
    return `${main} / ${active} active`;
  }
  return main;
}

function formatContextLength(ctx: number | null): string {
  if (!ctx) return '';
  if (ctx >= 1000000) return `${(ctx / 1000000).toFixed(0)}M tokens`;
  if (ctx >= 1000) return `${Math.round(ctx / 1000)}K tokens`;
  return `${ctx} tokens`;
}

export default function LoadModelDialog({
  modelId,
  modelName,
  serverType,
  size,
  quantization,
  params_b,
  active_params_b,
  context_length,
  reasoning_format,
  tool_use,
  open,
  onOpenChange,
  onLoaded,
}: Props) {
  const [options, setOptions] = useState<LoadOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [selectedBackendType, setSelectedBackendType] = useState<string>('');
  const [selectedNode, setSelectedNode] = useState<string>('');
  const [selectedContext, setSelectedContext] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const compatibleTypes = serverType.filter((t) => t in BACKEND_TYPE_LABELS);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setLoadingOptions(true);
    if (compatibleTypes.length > 0) {
      setSelectedBackendType(compatibleTypes[0]);
    }
    api
      .get(`/llm/models/${encodeURIComponent(modelId)}/load-options`)
      .then((res) => {
        const opts: LoadOptions = res.data;
        setOptions(opts);
        if (opts.gpu_nodes.length > 0) {
          setSelectedNode(opts.gpu_nodes[0].name);
        }
        const ctx = opts.context_length || context_length;
        if (ctx) {
          setSelectedContext(String(Math.min(ctx, DEFAULT_CONTEXT)));
        }
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to fetch load options');
      })
      .finally(() => setLoadingOptions(false));
  }, [open, modelId]);

  const handleLoad = async () => {
    setLoading(true);
    setError(null);
    try {
      const maxCtx = selectedContext ? parseInt(selectedContext, 10) : undefined;
      const resp = await api.post(
        `/llm/models/${encodeURIComponent(modelId)}/load`,
        {
          backend: selectedBackendType || undefined,
          node: selectedNode || undefined,
          max_context_length: maxCtx || undefined,
        }
      );
      if (
        resp.data?.state !== 'available' &&
        resp.data?.state !== 'loading'
      ) {
        setError(resp.data?.message || 'Failed to load model');
      } else {
        onOpenChange(false);
        onLoaded();
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load model');
    } finally {
      setLoading(false);
    }
  };

  const selectedNodeData = options?.gpu_nodes.find(
    (n) => n.name === selectedNode
  );
  const estimatedMem = options?.estimated_memory_gb || 0;
  const projectedUsage = selectedNodeData
    ? selectedNodeData.used_memory_gb + estimatedMem
    : 0;
  const gpusNeeded = selectedNodeData && selectedNodeData.per_gpu_memory_gb > 0 && !selectedNodeData.shared_memory
    ? Math.ceil(estimatedMem / (selectedNodeData.per_gpu_memory_gb * 0.85))
    : 1;

  return (
    <TkDialogRoot open={open} onOpenChange={onOpenChange}>
      <TkDialogContent className="max-w-lg">
        <TkDialogHeader>
          <TkDialogTitle>Load Model</TkDialogTitle>
        </TkDialogHeader>

        {loadingOptions ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin" />
          </div>
        ) : options ? (
          <div className="space-y-5">
            {/* Model info */}
            <div className="rounded-lg border p-3 space-y-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium">{modelName}</span>
                {reasoning_format && (
                  <TkBadge appearance="muted" className="text-xs">{reasoning_format}</TkBadge>
                )}
                {tool_use && (
                  <TkBadge appearance="muted" className="text-xs">tools</TkBadge>
                )}
              </div>
              <div className="flex gap-2 text-sm text-muted-foreground flex-wrap">
                {params_b ? (
                  <span>{formatParams(params_b, active_params_b)}</span>
                ) : size ? (
                  <span>{size}</span>
                ) : null}
                {quantization && (
                  <TkBadge appearance="outlined">{quantization}</TkBadge>
                )}
                {formatContextLength(context_length) && (
                  <span>{formatContextLength(context_length)}</span>
                )}
              </div>
              <div className="flex items-center gap-1 text-sm">
                <Zap className="w-3.5 h-3.5" />
                <span>
                  Estimated memory: {options.estimated_memory_gb.toFixed(1)} GB
                </span>
              </div>
            </div>

            {/* Node selection */}
            <div className="space-y-2">
              <TkLabel>Target GPU Node</TkLabel>
              {options.gpu_nodes.length === 0 ? (
                <div className="text-sm text-muted-foreground">
                  No GPU nodes discovered
                </div>
              ) : (
                <TkSelect
                  value={selectedNode}
                  onValueChange={setSelectedNode}
                >
                  <TkSelectTrigger>
                    <TkSelectValue placeholder="Select GPU node" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {options.gpu_nodes.map((n) => (
                      <TkSelectItem key={n.name} value={n.name}>
                        {n.name} — {n.gpu_count > 1 ? `${n.gpu_count}x ` : ''}{formatGpuProduct(n.gpu_product)} ({n.per_gpu_memory_gb.toFixed(0)}GB{n.gpu_count > 1 ? ' each' : ''})
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              )}
            </div>

            {/* Backend type selection */}
            {compatibleTypes.length > 1 && (
              <div className="space-y-2">
                <TkLabel>Backend Type</TkLabel>
                <TkSelect
                  value={selectedBackendType}
                  onValueChange={setSelectedBackendType}
                >
                  <TkSelectTrigger>
                    <TkSelectValue placeholder="Select backend type" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {compatibleTypes.map((t) => (
                      <TkSelectItem key={t} value={t}>
                        {BACKEND_TYPE_LABELS[t] || t}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              </div>
            )}

            {/* Context length selector */}
            {(options?.context_length || context_length) && (options?.context_length || context_length)! > 4096 && (
              <div className="space-y-2">
                <TkLabel>Context Length</TkLabel>
                <TkSelect
                  value={selectedContext}
                  onValueChange={setSelectedContext}
                >
                  <TkSelectTrigger>
                    <TkSelectValue placeholder="Select context length" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {CONTEXT_OPTIONS
                      .filter((opt) => opt.value <= (options?.context_length || context_length || 0))
                      .map((opt) => (
                        <TkSelectItem key={opt.value} value={String(opt.value)}>
                          {opt.label} tokens
                          {opt.value === (options?.context_length || context_length) ? ' (max)' : ''}
                        </TkSelectItem>
                      ))}
                  </TkSelectContent>
                </TkSelect>
                {selectedContext && parseInt(selectedContext, 10) >= LARGE_CONTEXT_THRESHOLD && (
                  <p className="text-xs text-warning font-medium">
                    Large context lengths require significantly more GPU memory for KV cache. 8K-16K recommended for most use cases.
                  </p>
                )}
                {selectedContext && parseInt(selectedContext, 10) < (options?.context_length || context_length || 0) && parseInt(selectedContext, 10) < LARGE_CONTEXT_THRESHOLD && (
                  <p className="text-xs text-muted-foreground">
                    Reduced context uses less GPU memory (less KV cache)
                  </p>
                )}
              </div>
            )}

            {/* GPU impact preview */}
            {selectedNodeData && (
              <div className="rounded-lg border p-4 space-y-3">
                <div>
                  <div className="font-medium">{selectedNodeData.name}</div>
                  <div className="text-sm text-muted-foreground">
                    {selectedNodeData.gpu_count > 1
                      ? `${selectedNodeData.gpu_count}x `
                      : ''}
                    {formatGpuProduct(selectedNodeData.gpu_product)}
                    {' '}({selectedNodeData.per_gpu_memory_gb.toFixed(0)}GB{selectedNodeData.gpu_count > 1 ? ' each' : ''})
                    {selectedNodeData.shared_memory ? ' — Time-sliced' : ''}
                  </div>
                </div>
                {selectedNodeData.is_uma || selectedNodeData.per_gpu_metrics.length === 0 ? (
                  <LoadPreviewBar
                    label={selectedNodeData.is_uma ? 'Shared Memory' : 'GPU Memory'}
                    usedGb={selectedNodeData.used_memory_gb}
                    totalGb={selectedNodeData.total_memory_gb}
                    modelGb={estimatedMem}
                  />
                ) : (
                  selectedNodeData.per_gpu_metrics.map(gpu => (
                    <LoadPreviewBar
                      key={gpu.index}
                      label={`GPU ${gpu.index}`}
                      usedGb={gpu.memory_used_mb / 1024}
                      totalGb={gpu.memory_total_mb / 1024}
                    />
                  ))
                )}
                {!selectedNodeData.shared_memory && gpusNeeded > 1 && (
                  <div className="text-sm text-warning">
                    Requires {gpusNeeded} GPUs (tensor parallelism)
                  </div>
                )}
                {!selectedNodeData.shared_memory && gpusNeeded > selectedNodeData.available_slots && (
                  <div className="text-sm text-destructive">
                    Not enough GPUs available ({selectedNodeData.available_slots} free)
                  </div>
                )}
                <div className="flex items-center justify-between text-sm text-muted-foreground">
                  <span>Model: {estimatedMem.toFixed(1)} GB</span>
                  <span>GPUs: {selectedNodeData.available_slots} / {selectedNodeData.total_slots} available</span>
                </div>
              </div>
            )}

            {error && (
              <div className="text-sm text-destructive">{error}</div>
            )}
          </div>
        ) : error ? (
          <div className="text-sm text-destructive py-4">{error}</div>
        ) : null}

        <TkDialogFooter>
          <TkButton
            intent="secondary"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            Cancel
          </TkButton>
          <TkButton
            onClick={handleLoad}
            disabled={
              loading ||
              loadingOptions ||
              !options ||
              !selectedNode ||
              !selectedBackendType
            }
          >
            {loading ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : null}
            Load Model
          </TkButton>
        </TkDialogFooter>
      </TkDialogContent>
    </TkDialogRoot>
  );
}

function LoadPreviewBar({
  label,
  usedGb,
  totalGb,
  modelGb,
}: {
  label: string;
  usedGb: number;
  totalGb: number;
  modelGb?: number;
}) {
  const pct = totalGb > 0 ? (usedGb / totalGb) * 100 : 0;
  const projPct = modelGb && totalGb > 0 ? ((usedGb + modelGb) / totalGb) * 100 : 0;
  const getColor = (p: number) => {
    if (p < 50) return 'bg-emerald-500';
    if (p < 75) return 'bg-amber-500';
    return 'bg-red-500';
  };
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span>{usedGb.toFixed(1)} / {totalGb.toFixed(0)} GB</span>
      </div>
      <div className="h-2.5 bg-muted rounded-full overflow-hidden relative">
        {modelGb != null && projPct > 0 && (
          <div
            className="absolute h-full rounded-full bg-blue-500/30"
            style={{ width: `${Math.min(projPct, 100)}%` }}
          />
        )}
        <div
          className={`relative h-full rounded-full transition-all duration-500 ${getColor(pct)}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}
