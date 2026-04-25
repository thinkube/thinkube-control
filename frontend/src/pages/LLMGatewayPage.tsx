import { useState, useEffect, useCallback } from 'react';
import { TkCard, TkCardContent, TkCardHeader, TkCardTitle, TkStatCard } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
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
} from 'lucide-react';
import api from '../lib/axios';

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
  estimated_memory_gb: number;
}

interface GPUNode {
  name: string;
  total_memory_gb: number;
  used_memory_gb: number;
  allocations: GPUAllocation[];
}

interface GPUStatus {
  nodes: GPUNode[];
  total_memory_gb: number;
  used_memory_gb: number;
  memory_threshold: number;
  can_accept_new_model: boolean;
}

export default function LLMGatewayPage() {
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [backends, setBackends] = useState<BackendEntry[]>([]);
  const [gpuStatus, setGpuStatus] = useState<GPUStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);

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

  const handleLoad = async (modelId: string) => {
    setActionLoading(prev => ({ ...prev, [modelId]: true }));
    try {
      await api.post(`/llm/models/${modelId}/load`, {});
      await fetchAll();
    } catch (err: any) {
      setError(err.response?.data?.detail || `Failed to load ${modelId}`);
    } finally {
      setActionLoading(prev => ({ ...prev, [modelId]: false }));
    }
  };

  const handleUnload = async (modelId: string) => {
    setActionLoading(prev => ({ ...prev, [modelId]: true }));
    try {
      await api.post(`/llm/models/${modelId}/unload`, {});
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

  const availableModels = models.filter(m => m.state === 'available');
  const loadedOnBackends = backends.filter(b => b.models.length > 0);
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
          description={`${models.length} total in registry`}
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

      {/* GPU Allocations */}
      {gpuStatus && gpuStatus.nodes.length > 0 && (
        <TkCard className="mb-6">
          <TkCardHeader>
            <TkCardTitle>GPU Allocations</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            {gpuStatus.nodes.map(node => (
              <div key={node.name} className="mb-4 last:mb-0">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium">{node.name}</span>
                  <span className="text-sm text-muted-foreground">
                    {node.used_memory_gb.toFixed(1)} / {node.total_memory_gb.toFixed(1)} GB
                  </span>
                </div>
                <div className="w-full bg-muted rounded-full h-3 mb-2">
                  <div
                    className="bg-primary rounded-full h-3 transition-all"
                    style={{ width: `${Math.min(100, (node.used_memory_gb / node.total_memory_gb) * 100)}%` }}
                  />
                </div>
                {node.allocations.length > 0 ? (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {node.allocations.map(alloc => (
                      <TkBadge key={alloc.model_id} appearance="muted">
                        {alloc.model_id} ({alloc.estimated_memory_gb.toFixed(1)} GB on {alloc.backend_id})
                      </TkBadge>
                    ))}
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">No models loaded</div>
                )}
              </div>
            ))}
          </TkCardContent>
        </TkCard>
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
          {models.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No models in registry
            </div>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>Model</TkTableHead>
                  <TkTableHead>Size</TkTableHead>
                  <TkTableHead>Quantization</TkTableHead>
                  <TkTableHead>Backend Type</TkTableHead>
                  <TkTableHead>State</TkTableHead>
                  <TkTableHead>Running On</TkTableHead>
                  <TkTableHead className="text-right">Actions</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {models.map(model => (
                  <TkTableRow key={model.id}>
                    <TkTableCell className="font-medium">
                      <div>
                        <div className="flex items-center gap-2">
                          {model.name}
                          {model.is_finetuned && (
                            <TkBadge appearance="muted" className="text-xs">Fine-tuned</TkBadge>
                          )}
                        </div>
                        {model.description && (
                          <div className="text-sm text-muted-foreground">{model.description}</div>
                        )}
                      </div>
                    </TkTableCell>
                    <TkTableCell>{model.size || '-'}</TkTableCell>
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
                        <TkBadge appearance="muted">{model.backend_id}</TkBadge>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TkTableCell>
                    <TkTableCell className="text-right">
                      <div className="flex gap-2 justify-end">
                        {model.state === 'available' ? (
                          <TkButton
                            intent="secondary"
                            size="sm"
                            onClick={() => handleUnload(model.id)}
                            disabled={!!actionLoading[model.id]}
                          >
                            {actionLoading[model.id] ? (
                              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                            ) : (
                              <Square className="w-4 h-4 mr-2" />
                            )}
                            Unload
                          </TkButton>
                        ) : model.state === 'loading' ? (
                          <TkBadge status="active">
                            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                            Loading...
                          </TkBadge>
                        ) : model.state === 'unloading' ? (
                          <TkBadge status="pending">
                            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                            Unloading...
                          </TkBadge>
                        ) : (model.state === 'deployable' || model.state === 'registered') ? (
                          <TkButton
                            size="sm"
                            onClick={() => handleLoad(model.id)}
                            disabled={!!actionLoading[model.id]}
                          >
                            {actionLoading[model.id] ? (
                              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                            ) : (
                              <Play className="w-4 h-4 mr-2" />
                            )}
                            Load
                          </TkButton>
                        ) : null}
                      </div>
                    </TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          )}
        </TkCardContent>
      </TkCard>
    </TkPageWrapper>
  );
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
