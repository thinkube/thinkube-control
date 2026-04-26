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

interface LoadOptionBackend {
  id: string;
  name: string;
  type: string;
  status: string;
  node: string | null;
}

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
  used_memory_gb: number;
  shared_memory: boolean;
  allocations: GPUAllocation[];
}

interface LoadOptions {
  model_id: string;
  compatible_backends: LoadOptionBackend[];
  gpu_nodes: GPUNode[];
  estimated_memory_gb: number;
}

interface Props {
  modelId: string;
  modelName: string;
  size: string | null;
  quantization: string | null;
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

export default function LoadModelDialog({
  modelId,
  modelName,
  size,
  quantization,
  open,
  onOpenChange,
  onLoaded,
}: Props) {
  const [options, setOptions] = useState<LoadOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [selectedBackend, setSelectedBackend] = useState<string>('');
  const [selectedNode, setSelectedNode] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setLoadingOptions(true);
    api
      .get(`/llm/models/${encodeURIComponent(modelId)}/load-options`)
      .then((res) => {
        const opts: LoadOptions = res.data;
        setOptions(opts);
        const firstBackend = opts.compatible_backends[0];
        if (firstBackend) {
          setSelectedBackend(firstBackend.id);
          if (firstBackend.node) {
            setSelectedNode(firstBackend.node);
          } else if (opts.gpu_nodes.length > 0) {
            setSelectedNode(opts.gpu_nodes[0].name);
          }
        } else if (opts.gpu_nodes.length > 0) {
          setSelectedNode(opts.gpu_nodes[0].name);
        }
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to fetch load options');
      })
      .finally(() => setLoadingOptions(false));
  }, [open, modelId]);

  useEffect(() => {
    if (!selectedBackend || !options) return;
    const backend = options.compatible_backends.find(b => b.id === selectedBackend);
    if (backend?.node) {
      setSelectedNode(backend.node);
    }
  }, [selectedBackend, options]);

  const selectedBackendData = options?.compatible_backends.find(
    (b) => b.id === selectedBackend
  );
  const nodeIsLocked = !!selectedBackendData?.node;

  const handleNodeChange = (nodeName: string) => {
    setSelectedNode(nodeName);
    if (!options) return;
    const backendOnNode = options.compatible_backends.find(
      (b) => b.node === nodeName
    );
    if (backendOnNode) {
      setSelectedBackend(backendOnNode.id);
    }
  };

  const handleBackendChange = (backendId: string) => {
    setSelectedBackend(backendId);
    if (!options) return;
    const backend = options.compatible_backends.find((b) => b.id === backendId);
    if (backend?.node) {
      setSelectedNode(backend.node);
    }
  };

  const handleLoad = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.post(
        `/llm/models/${encodeURIComponent(modelId)}/load`,
        {
          backend: selectedBackend || undefined,
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
  const projectedUsage = selectedNodeData
    ? selectedNodeData.used_memory_gb + (options?.estimated_memory_gb || 0)
    : 0;

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
            <div className="rounded-lg border p-3 space-y-1">
              <div className="font-medium">{modelName}</div>
              <div className="flex gap-2 text-sm text-muted-foreground">
                {size && <span>{size}</span>}
                {quantization && (
                  <TkBadge appearance="outlined">{quantization}</TkBadge>
                )}
              </div>
              <div className="flex items-center gap-1 text-sm">
                <Zap className="w-3.5 h-3.5" />
                <span>
                  Estimated memory: {options.estimated_memory_gb.toFixed(1)} GB
                </span>
              </div>
            </div>

            {/* Backend selection */}
            <div className="space-y-2">
              <TkLabel>Backend</TkLabel>
              {options.compatible_backends.length === 0 ? (
                <div className="text-sm text-muted-foreground">
                  No compatible backends available
                </div>
              ) : (
                <TkSelect
                  value={selectedBackend}
                  onValueChange={handleBackendChange}
                >
                  <TkSelectTrigger>
                    <TkSelectValue placeholder="Select backend" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {options.compatible_backends.map((b) => (
                      <TkSelectItem key={b.id} value={b.id}>
                        {b.name} — {b.status}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              )}
            </div>

            {/* Node selection */}
            <div className="space-y-2">
              <TkLabel>Target GPU Node</TkLabel>
              {options.gpu_nodes.length === 0 ? (
                <div className="text-sm text-muted-foreground">
                  No GPU nodes discovered
                </div>
              ) : (
                <>
                  <TkSelect
                    value={selectedNode}
                    onValueChange={handleNodeChange}
                    disabled={nodeIsLocked}
                  >
                    <TkSelectTrigger>
                      <TkSelectValue placeholder="Select GPU node" />
                    </TkSelectTrigger>
                    <TkSelectContent>
                      {options.gpu_nodes.map((n) => (
                        <TkSelectItem key={n.name} value={n.name}>
                          {n.name} — {formatGpuProduct(n.gpu_product)} (
                          {n.used_memory_gb.toFixed(1)} / {n.total_memory_gb.toFixed(0)} GB)
                        </TkSelectItem>
                      ))}
                    </TkSelectContent>
                  </TkSelect>
                  {nodeIsLocked && (
                    <p className="text-xs text-muted-foreground">
                      Node is determined by the selected backend
                    </p>
                  )}
                </>
              )}
            </div>

            {/* GPU impact preview */}
            {selectedNodeData && (
              <div className="rounded-lg border p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <div className="font-medium">{selectedNodeData.name}</div>
                    <div className="text-sm text-muted-foreground">
                      {formatGpuProduct(selectedNodeData.gpu_product)}
                      {selectedNodeData.gpu_count > 1
                        ? ` (${selectedNodeData.gpu_count}x)`
                        : ''}
                      {selectedNodeData.shared_memory ? ' — Time-sliced' : ''}
                    </div>
                  </div>
                  <TkSemicircularGauge
                    value={projectedUsage}
                    max={selectedNodeData.total_memory_gb}
                    label="After Load"
                    unit="GB"
                    size={100}
                  />
                </div>
                <div className="text-sm text-muted-foreground">
                  Current: {selectedNodeData.used_memory_gb.toFixed(1)} GB
                  {' → '}
                  Projected: {projectedUsage.toFixed(1)} /{' '}
                  {selectedNodeData.total_memory_gb.toFixed(0)} GB
                </div>
                <div className="text-sm text-muted-foreground">
                  Slots: {selectedNodeData.available_slots} /{' '}
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
              options.compatible_backends.length === 0
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
