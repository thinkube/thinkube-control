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
import { TkSemicircularGauge } from 'thinkube-style/components/data-viz';
import { Loader2, Zap } from 'lucide-react';
import api from '../lib/axios';

interface GPUAllocation {
  model_id: string;
  backend_id: string;
  node_name: string;
  estimated_memory_gb: number;
  slots: number;
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
  allocations: GPUAllocation[];
}

interface LoadOptions {
  model_id: string;
  compatible_backends: { id: string; name: string; type: string; status: string; node: string | null }[];
  gpu_nodes: GPUNode[];
  estimated_memory_gb: number;
}

const BACKEND_TYPE_LABELS: Record<string, string> = {
  ollama: 'Ollama',
  vllm: 'vLLM',
  'tensorrt-llm': 'TensorRT-LLM',
};

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
      const resp = await api.post(
        `/llm/models/${encodeURIComponent(modelId)}/load`,
        {
          backend: selectedBackendType || undefined,
          node: selectedNode || undefined,
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

            {/* GPU impact preview */}
            {selectedNodeData && (
              <div className="rounded-lg border p-4">
                <div className="flex items-center justify-between mb-3">
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
                  <TkSemicircularGauge
                    value={estimatedMem}
                    max={selectedNodeData.shared_memory
                      ? selectedNodeData.total_memory_gb
                      : selectedNodeData.per_gpu_memory_gb * gpusNeeded}
                    label="Model"
                    unit="GB"
                    size={100}
                  />
                </div>
                {!selectedNodeData.shared_memory && gpusNeeded > 1 && (
                  <div className="text-sm text-warning mb-1">
                    Requires {gpusNeeded} GPUs (tensor parallelism)
                  </div>
                )}
                {!selectedNodeData.shared_memory && gpusNeeded > selectedNodeData.available_slots && (
                  <div className="text-sm text-destructive mb-1">
                    Not enough GPUs available ({selectedNodeData.available_slots} free)
                  </div>
                )}
                <div className="text-sm text-muted-foreground">
                  Model: {estimatedMem.toFixed(1)}GB
                  {' / '}
                  {selectedNodeData.shared_memory
                    ? `${selectedNodeData.total_memory_gb.toFixed(0)}GB pool`
                    : gpusNeeded > 1
                      ? `${gpusNeeded}x ${selectedNodeData.per_gpu_memory_gb.toFixed(0)}GB`
                      : `${selectedNodeData.per_gpu_memory_gb.toFixed(0)}GB GPU`
                  }
                </div>
                <div className="text-sm text-muted-foreground">
                  GPUs: {selectedNodeData.available_slots} /{' '}
                  {selectedNodeData.total_slots} available
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
