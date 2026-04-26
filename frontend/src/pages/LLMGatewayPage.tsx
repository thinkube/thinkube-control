import { useState, useEffect, useCallback } from 'react';
import { TkCard, TkCardContent, TkCardHeader, TkCardTitle, TkStatCard } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkSemicircularGauge } from 'thinkube-style/components/data-viz';
import {
  TkTable,
  TkTableBody,
  TkTableCell,
  TkTableHead,
  TkTableHeader,
  TkTableRow,
} from 'thinkube-style/components/tables';
import {
  Cpu,
  Loader2,
  CheckCircle2,
  XCircle,
  Play,
  Square,
  RefreshCw,
  Server,
  Zap,
  Copy,
  Check,
  Lock,
} from 'lucide-react';
import api from '../lib/axios';
import LoadModelDialog from '../components/LoadModelDialog';

interface ModelEntry {
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
}

interface BackendEntry {
  id: string;
  name: string;
  url: string;
  type: string;
  status: string;
  models: string[];
  last_probe: string | null;
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

interface GPUStatus {
  nodes: GPUNode[];
  total_memory_gb: number;
  used_memory_gb: number;
  memory_threshold: number;
  can_accept_new_model: boolean;
}

const LOADABLE_TYPES = new Set(['ollama', 'vllm', 'tensorrt-llm']);
const NON_LOADABLE_LABELS: Record<string, string> = {
  unsloth: 'Fine-tuning',
  'text-embeddings': 'Embeddings',
};

function isModelLoadable(serverTypes: string[]): boolean {
  return serverTypes.some((t) => LOADABLE_TYPES.has(t));
}

function getNonLoadableLabel(serverTypes: string[]): string | null {
  for (const t of serverTypes) {
    if (NON_LOADABLE_LABELS[t]) return NON_LOADABLE_LABELS[t];
  }
  return null;
}

function formatGpuProduct(product: string | null): string {
  if (!product) return 'GPU';
  return product.replace('NVIDIA-', '').replace('NVIDIA ', '').replace(/-/g, ' ');
}

function formatParams(params_b: number | null, active_params_b: number | null): string {
  if (!params_b) return '-';
  const main = params_b >= 1 ? `${params_b}B` : `${(params_b * 1000).toFixed(0)}M`;
  if (active_params_b) {
    const active = active_params_b >= 1 ? `${active_params_b}B` : `${(active_params_b * 1000).toFixed(0)}M`;
    return `${main} / ${active} active`;
  }
  return main;
}

function formatContextLength(ctx: number | null): string {
  if (!ctx) return '';
  if (ctx >= 1000000) return `${(ctx / 1000000).toFixed(0)}M ctx`;
  if (ctx >= 1000) return `${Math.round(ctx / 1000)}K ctx`;
  return `${ctx} ctx`;
}

export default function LLMGatewayPage() {
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [backends, setBackends] = useState<BackendEntry[]>([]);
  const [gpuStatus, setGpuStatus] = useState<GPUStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);
  const [loadDialog, setLoadDialog] = useState<ModelEntry | null>(null);

  const gatewayUrl = `https://llm.${window.location.hostname.split('.').slice(-2).join('.')}`;

  const fetchAll = useCallback(async () => {
    try {
      const [modelsRes, backendsRes, gpuRes] = await Promise.all([
        api.get('/llm/models/'),
        api.get('/llm/backends/'),
        api.get('/llm/gpu/status/'),
      ]);
      setModels(modelsRes.data.models || []);
      setBackends(backendsRes.data.backends || []);
      setGpuStatus(gpuRes.data);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to fetch LLM data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 10000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      await api.post('/llm/refresh/');
      await fetchAll();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Refresh failed');
      setLoading(false);
    }
  };

  const handleUnload = async (modelId: string) => {
    setActionLoading(prev => ({ ...prev, [modelId]: true }));
    setError(null);
    try {
      const resp = await api.post(`/llm/models/${encodeURIComponent(modelId)}/unload`, {});
      if (resp.data?.state === 'available') {
        setError(resp.data?.message || `Failed to unload ${modelId}`);
      }
      await fetchAll();
    } catch (err: any) {
      setError(err.response?.data?.detail || `Failed to unload ${modelId}`);
    } finally {
      setActionLoading(prev => ({ ...prev, [modelId]: false }));
    }
  };

  const handleCopyUrl = () => {
    navigator.clipboard.writeText(gatewayUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const gatewayModels = models.filter(m => isModelLoadable(m.server_type));
  const availableModels = models.filter(m => m.state === 'available');
  const healthyBackends = backends.filter(b => b.status === 'healthy');
  const memoryUsedPct = gpuStatus
    ? Math.round((gpuStatus.used_memory_gb / gpuStatus.total_memory_gb) * 100)
    : 0;

  if (loading && models.length === 0) {
    return (
      <TkPageWrapper>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin" />
        </div>
      </TkPageWrapper>
    );
  }

  return (
    <TkPageWrapper description="Unified API gateway for local LLM inference — OpenAI and Anthropic compatible">

      {error && <TkErrorAlert title="Error" className="mb-6">{error}</TkErrorAlert>}

      {/* Gateway URL */}
      <TkCard className="mb-6">
        <TkCardContent className="pt-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-muted-foreground mb-1">Gateway Endpoint</div>
              <code className="text-lg font-mono">{gatewayUrl}</code>
              <div className="text-xs text-muted-foreground mt-1">
                OpenAI: <code>/v1/chat/completions</code> · Anthropic: <code>/v1/messages</code>
              </div>
            </div>
            <TkButton intent="secondary" size="sm" onClick={handleCopyUrl}>
              {copied ? <Check className="w-4 h-4 mr-2" /> : <Copy className="w-4 h-4 mr-2" />}
              {copied ? 'Copied' : 'Copy URL'}
            </TkButton>
          </div>
        </TkCardContent>
      </TkCard>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <TkStatCard
          title="Available Models"
          value={availableModels.length}
          description={`${gatewayModels.length} loadable, ${models.length} total in registry`}
          icon={Cpu}
          variant="primary"
        />
        <TkStatCard
          title="Backends"
          value={healthyBackends.length}
          description={`${backends.length} total, ${healthyBackends.length} healthy`}
          icon={Server}
          variant="primary"
        />
        <TkStatCard
          title="GPU Memory"
          value={gpuStatus ? `${gpuStatus.used_memory_gb.toFixed(1)} / ${gpuStatus.total_memory_gb.toFixed(1)} GB` : '-'}
          description={gpuStatus ? `${memoryUsedPct}% used` : 'Loading...'}
          icon={Zap}
          variant={memoryUsedPct > 90 ? 'warning' : 'primary'}
        />
        <TkStatCard
          title="Can Load More"
          value={gpuStatus?.can_accept_new_model ? 'Yes' : 'No'}
          description={gpuStatus ? `Threshold: ${(gpuStatus.memory_threshold * 100).toFixed(0)}%` : ''}
          icon={gpuStatus?.can_accept_new_model ? CheckCircle2 : XCircle}
          variant={gpuStatus?.can_accept_new_model ? 'primary' : 'warning'}
        />
      </div>

      {/* GPU Nodes */}
      {gpuStatus && gpuStatus.nodes.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          {gpuStatus.nodes.map(node => (
            <TkCard key={node.name}>
              <TkCardContent className="pt-6">
                <div className="flex items-start justify-between">
                  <div className="space-y-1.5 flex-1">
                    <div className="font-medium text-lg">{node.name}</div>
                    <div className="flex flex-wrap gap-1.5">
                      <TkBadge appearance="outlined">
                        {formatGpuProduct(node.gpu_product)}
                      </TkBadge>
                      {node.gpu_family && (
                        <TkBadge appearance="muted">{node.gpu_family}</TkBadge>
                      )}
                      {node.gpu_count > 1 && (
                        <TkBadge appearance="muted">{node.gpu_count}x GPU</TkBadge>
                      )}
                      {node.shared_memory && (
                        <TkBadge appearance="muted">Time-sliced</TkBadge>
                      )}
                    </div>
                    <div className="text-sm text-muted-foreground pt-1">
                      {node.used_memory_gb.toFixed(1)} / {node.total_memory_gb.toFixed(0)} GB used
                    </div>
                    <div className="text-sm text-muted-foreground">
                      Slots: {node.available_slots} / {node.total_slots} available
                    </div>
                    {node.allocations.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-2">
                        {node.allocations.map(alloc => (
                          <TkBadge key={alloc.model_id} status="healthy">
                            {alloc.model_id.split('/').pop()} ({alloc.estimated_memory_gb.toFixed(1)} GB)
                          </TkBadge>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="ml-4 flex-shrink-0">
                    <TkSemicircularGauge
                      value={node.used_memory_gb}
                      max={node.total_memory_gb}
                      label="Memory"
                      unit="GB"
                      size={120}
                    />
                  </div>
                </div>
              </TkCardContent>
            </TkCard>
          ))}
        </div>
      )}

      {/* Backends */}
      <TkCard className="mb-6">
        <TkCardHeader className="flex flex-row items-center justify-between">
          <TkCardTitle>Inference Backends</TkCardTitle>
          <TkButton intent="secondary" size="sm" onClick={handleRefresh} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </TkButton>
        </TkCardHeader>
        <TkCardContent>
          {backends.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No backends discovered
            </div>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Backend</TkTableHead>
                  <TkTableHead>Type</TkTableHead>
                  <TkTableHead>Status</TkTableHead>
                  <TkTableHead>Loaded Models</TkTableHead>
                  <TkTableHead>Last Probe</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {backends.map(backend => (
                  <TkTableRow key={backend.id}>
                    <TkTableCell className="font-medium">
                      <div>
                        <div>{backend.name}</div>
                        <div className="text-xs text-muted-foreground font-mono">{backend.url}</div>
                      </div>
                    </TkTableCell>
                    <TkTableCell>
                      <TkBadge appearance="outlined">{backend.type}</TkBadge>
                    </TkTableCell>
                    <TkTableCell>
                      <TkBadge status={backend.status === 'healthy' ? 'healthy' : 'unhealthy'}>
                        {backend.status === 'healthy' ? (
                          <CheckCircle2 className="w-3 h-3 mr-1" />
                        ) : (
                          <XCircle className="w-3 h-3 mr-1" />
                        )}
                        {backend.status}
                      </TkBadge>
                    </TkTableCell>
                    <TkTableCell>
                      {backend.models.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {backend.models.map(m => (
                            <TkBadge key={m} appearance="muted">{m}</TkBadge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </TkTableCell>
                    <TkTableCell className="text-sm text-muted-foreground">
                      {backend.last_probe ? formatTimeAgo(backend.last_probe) : '-'}
                    </TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          )}
        </TkCardContent>
      </TkCard>

      {/* Models */}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>Models</TkCardTitle>
        </TkCardHeader>
        <TkCardContent>
          {gatewayModels.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No loadable models in registry
            </div>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Model</TkTableHead>
                  <TkTableHead>Params</TkTableHead>
                  <TkTableHead>Quantization</TkTableHead>
                  <TkTableHead>Backend Type</TkTableHead>
                  <TkTableHead>State</TkTableHead>
                  <TkTableHead>Running On</TkTableHead>
                  <TkTableHead className="text-right">Actions</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {gatewayModels.map(model => (
                  <TkTableRow key={model.id}>
                    <TkTableCell className="font-medium">
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          {model.name}
                          {model.is_finetuned && (
                            <TkBadge appearance="muted" className="text-xs">Fine-tuned</TkBadge>
                          )}
                          {model.reasoning_format && (
                            <TkBadge appearance="muted" className="text-xs">{model.reasoning_format}</TkBadge>
                          )}
                          {model.tool_use && (
                            <TkBadge appearance="muted" className="text-xs">tools</TkBadge>
                          )}
                          {model.gated && (
                            <TkBadge status="warning" className="text-xs">
                              <Lock className="w-3 h-3 mr-0.5" />gated
                            </TkBadge>
                          )}
                        </div>
                        {model.description && (
                          <div className="text-sm text-muted-foreground">{model.description}</div>
                        )}
                        <div className="flex gap-2 text-xs text-muted-foreground mt-0.5">
                          {formatContextLength(model.context_length) && (
                            <span>{formatContextLength(model.context_length)}</span>
                          )}
                          {model.license && <span>{model.license}</span>}
                        </div>
                      </div>
                    </TkTableCell>
                    <TkTableCell>
                      <div>
                        <div>{model.params_b ? formatParams(model.params_b, model.active_params_b) : (model.size || '-')}</div>
                        {model.params_b && model.size && (
                          <div className="text-xs text-muted-foreground">{model.size}</div>
                        )}
                      </div>
                    </TkTableCell>
                    <TkTableCell>
                      {model.quantization ? (
                        <TkBadge appearance="outlined">{model.quantization}</TkBadge>
                      ) : '-'}
                    </TkTableCell>
                    <TkTableCell>
                      <div className="flex gap-1 flex-wrap">
                        {model.server_type.map(type => (
                          <TkBadge key={type} appearance="muted">{type}</TkBadge>
                        ))}
                      </div>
                    </TkTableCell>
                    <TkTableCell>
                      <ModelStateBadge state={model.state} />
                    </TkTableCell>
                    <TkTableCell>
                      {model.backend_id ? (
                        <TkBadge appearance="muted">
                          {model.backend_id.startsWith('ollama-')
                            ? model.backend_id.replace('ollama-', '')
                            : model.backend_id}
                        </TkBadge>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TkTableCell>
                    <TkTableCell className="text-right">
                      <ModelActions
                        model={model}
                        actionLoading={!!actionLoading[model.id]}
                        onLoad={() => setLoadDialog(model)}
                        onUnload={() => handleUnload(model.id)}
                      />
                    </TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          )}
        </TkCardContent>
      </TkCard>

      {/* Load Model Dialog */}
      {loadDialog && (
        <LoadModelDialog
          modelId={loadDialog.id}
          modelName={loadDialog.name}
          size={loadDialog.size}
          quantization={loadDialog.quantization}
          params_b={loadDialog.params_b}
          active_params_b={loadDialog.active_params_b}
          context_length={loadDialog.context_length}
          reasoning_format={loadDialog.reasoning_format}
          tool_use={loadDialog.tool_use ?? false}
          open={!!loadDialog}
          onOpenChange={(open) => {
            if (!open) setLoadDialog(null);
          }}
          onLoaded={() => {
            setLoadDialog(null);
            fetchAll();
          }}
        />
      )}
    </TkPageWrapper>
  );
}

function ModelActions({
  model,
  actionLoading,
  onLoad,
  onUnload,
}: {
  model: ModelEntry;
  actionLoading: boolean;
  onLoad: () => void;
  onUnload: () => void;
}) {
  if (model.state === 'available') {
    return (
      <div className="flex gap-2 justify-end">
        <TkButton
          intent="secondary"
          size="sm"
          onClick={onUnload}
          disabled={actionLoading}
        >
          {actionLoading ? (
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          ) : (
            <Square className="w-4 h-4 mr-2" />
          )}
          Unload
        </TkButton>
      </div>
    );
  }

  if (model.state === 'loading') {
    return (
      <TkBadge status="active">
        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
        Loading...
      </TkBadge>
    );
  }

  if (model.state === 'unloading') {
    return (
      <TkBadge status="pending">
        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
        Unloading...
      </TkBadge>
    );
  }

  if (model.state === 'deployable' || model.state === 'registered') {
    if (!isModelLoadable(model.server_type)) {
      const label = getNonLoadableLabel(model.server_type);
      return label ? (
        <TkBadge appearance="muted">{label}</TkBadge>
      ) : null;
    }

    return (
      <div className="flex flex-col items-end gap-1">
        <TkButton size="sm" onClick={onLoad} disabled={actionLoading}>
          {actionLoading ? (
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          ) : (
            <Play className="w-4 h-4 mr-2" />
          )}
          Load
        </TkButton>
        {model.last_error && (
          <span className="text-xs text-destructive max-w-[200px] text-right">
            {model.last_error}
          </span>
        )}
      </div>
    );
  }

  return null;
}

function ModelStateBadge({ state }: { state: string }) {
  switch (state) {
    case 'available':
      return (
        <TkBadge status="healthy">
          <CheckCircle2 className="w-3 h-3 mr-1" />
          Available
        </TkBadge>
      );
    case 'loading':
      return (
        <TkBadge status="active">
          <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          Loading
        </TkBadge>
      );
    case 'unloading':
      return (
        <TkBadge status="pending">
          <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          Unloading
        </TkBadge>
      );
    case 'deployable':
      return <TkBadge status="pending">Deployable</TkBadge>;
    case 'registered':
      return <TkBadge appearance="outlined">Registered</TkBadge>;
    default:
      return <TkBadge appearance="outlined">{state}</TkBadge>;
  }
}

function formatTimeAgo(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}
