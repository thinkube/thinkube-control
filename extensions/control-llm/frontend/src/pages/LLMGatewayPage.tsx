/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { TkCard, TkCardContent, TkCardHeader, TkCardTitle, TkStatCard } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkTable, TkTableBody, TkTableCell, TkTableHead, TkTableHeader, TkTableRow } from 'thinkube-style/components/tables';
import { Cpu, Loader2, CheckCircle2, XCircle, Play, Square, RefreshCw, Server, Zap, Copy, Check, Lock } from 'lucide-react';
import api from '@/lib/axios';
import { errorText } from '@/lib/errorText';
import LoadModelDialog from '@/components/llm/LoadModelDialog';
import {
  barColor,
  formatContextValue,
  formatGpuProduct,
  formatParams,
  type BackendEntry,
  type BackendType,
  type GPUNode,
  type GPUStatus,
  type ModelEntry,
  type TFunction,
} from '@/components/llm/types';

/** How often the page reads the models, backends and GPUs again. */
export const POLL_INTERVAL_MS = 10000;

/** How long the copy button says the URL was copied. */
const COPIED_MS = 2000;

/** server_type values that name no gateway backend, with the key of what the model is for. */
const NON_LOADABLE_LABELS: Record<string, string> = {
  unsloth: 'llm.nonLoadable.unsloth',
};

function gatewayTypes(serverTypes: string[]): string[] {
  return serverTypes.filter((type) => !(type in NON_LOADABLE_LABELS));
}

function isModelLoadable(serverTypes: string[]): boolean {
  return gatewayTypes(serverTypes).length > 0;
}

function nonLoadableLabel(serverTypes: string[]): string | null {
  const type = serverTypes.find((s) => s in NON_LOADABLE_LABELS);
  return type ? NON_LOADABLE_LABELS[type] : null;
}

/** The gateway answers at llm.<domain>, the domain of the page that shows it. */
function gatewayUrl(): string {
  return `https://llm.${window.location.hostname.split('.').slice(-2).join('.')}`;
}

/** The LLM gateway: its endpoint, the GPU nodes, the backends, and the models loaded and unloaded here. */
export default function LLMGatewayPage() {
  const { t } = useTranslation();
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [backends, setBackends] = useState<BackendEntry[]>([]);
  const [gpuStatus, setGpuStatus] = useState<GPUStatus | null>(null);
  const [backendTypes, setBackendTypes] = useState<BackendType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);
  const [loadDialog, setLoadDialog] = useState<ModelEntry | null>(null);

  const url = gatewayUrl();

  const fetchAll = useCallback(async () => {
    try {
      const [modelsRes, backendsRes, gpuRes, typesRes] = await Promise.all([
        api.get('/llm/models/'),
        api.get('/llm/backends/'),
        api.get('/llm/gpu/status/'),
        api.get('/llm/backends/types'),
      ]);
      setModels(modelsRes.data.models);
      setBackends(backendsRes.data.backends);
      setGpuStatus(gpuRes.data);
      setBackendTypes(typesRes.data.backend_types);
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchAll]);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      await api.post('/llm/refresh/');
      await fetchAll();
    } catch (err) {
      setError(errorText(err));
      setLoading(false);
    }
  };

  const handleUnload = async (modelId: string) => {
    setActionLoading((prev) => ({ ...prev, [modelId]: true }));
    setError(null);
    try {
      // The model's id holds a slash, which the API's path takes as it is
      const resp = await api.post(`/llm/models/${modelId}/unload`, {});
      // The API answers 200 when it refuses an unload too; its message says why
      if (resp.data.state === 'available') {
        setError(resp.data.message);
      }
      await fetchAll();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setActionLoading((prev) => ({ ...prev, [modelId]: false }));
    }
  };

  const handleCopyUrl = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), COPIED_MS);
    } catch (err) {
      setError(errorText(err));
    }
  };

  const gatewayModels = models.filter((m) => isModelLoadable(m.server_type));
  const availableModels = models.filter((m) => m.state === 'available');
  const healthyBackends = backends.filter((b) => b.status === 'healthy');
  const memoryUsedPct = gpuStatus ? Math.round((gpuStatus.used_memory_gb / gpuStatus.total_memory_gb) * 100) : 0;
  const installedBackendTypes = backendTypes.map((b) => b.type);

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
    <TkPageWrapper description={t('llm.description')}>
      {error && (
        <TkErrorAlert title={t('llm.errorTitle')} className="mb-6">
          {error}
        </TkErrorAlert>
      )}

      <TkCard className="mb-6">
        <TkCardContent className="pt-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-muted-foreground mb-1">{t('llm.endpoint.title')}</div>
              <code className="text-lg font-mono">{url}</code>
              <div className="text-xs text-muted-foreground mt-1">
                OpenAI: <code>/v1/chat/completions</code> · Anthropic: <code>/v1/messages</code>
              </div>
            </div>
            <TkButton intent="secondary" size="sm" onClick={handleCopyUrl}>
              {copied ? <Check className="w-4 h-4 mr-2" /> : <Copy className="w-4 h-4 mr-2" />}
              {copied ? t('llm.endpoint.copied') : t('llm.endpoint.copy')}
            </TkButton>
          </div>
        </TkCardContent>
      </TkCard>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] gap-4 mb-6">
        <TkStatCard
          title={t('llm.stats.availableModels')}
          value={availableModels.length}
          description={t('llm.stats.availableModelsDescription', {
            loadable: gatewayModels.length,
            total: models.length,
          })}
          icon={Cpu}
          variant="primary"
        />
        <TkStatCard
          title={t('llm.stats.backends')}
          value={healthyBackends.length}
          description={t('llm.stats.backendsDescription', { total: backends.length, active: healthyBackends.length })}
          icon={Server}
          variant="primary"
        />
        <TkStatCard
          title={t('llm.stats.gpuMemory')}
          value={
            gpuStatus ? `${gpuStatus.used_memory_gb.toFixed(1)} / ${gpuStatus.total_memory_gb.toFixed(1)} GB` : '-'
          }
          description={gpuStatus ? t('llm.stats.used', { pct: memoryUsedPct }) : t('llm.stats.loading')}
          icon={Zap}
          variant={memoryUsedPct > 90 ? 'warning' : 'primary'}
        />
        <TkStatCard
          title={t('llm.stats.canLoadMore')}
          value={gpuStatus?.can_accept_new_model ? t('common.yes') : t('common.no')}
          description={
            gpuStatus ? t('llm.stats.threshold', { pct: (gpuStatus.memory_threshold * 100).toFixed(0) }) : ''
          }
          icon={gpuStatus?.can_accept_new_model ? CheckCircle2 : XCircle}
          variant={gpuStatus?.can_accept_new_model ? 'primary' : 'warning'}
        />
      </div>

      {gpuStatus && gpuStatus.nodes.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          {gpuStatus.nodes.map((node) => (
            <TkCard key={node.name}>
              <TkCardContent className="pt-6">
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="font-medium text-lg">{node.name}</div>
                    <div className="flex flex-wrap gap-1.5">
                      <TkBadge appearance="outlined">{formatGpuProduct(t, node.gpu_product)}</TkBadge>
                      {node.gpu_family && <TkBadge appearance="muted">{node.gpu_family}</TkBadge>}
                      {node.shared_memory && <TkBadge appearance="muted">{t('llm.gpu.timeSliced')}</TkBadge>}
                    </div>
                  </div>
                  <GPUMemoryBars t={t} node={node} />
                  <div className="flex items-center justify-between text-sm text-muted-foreground">
                    <span>{t('llm.gpu.slots', { available: node.available_slots, total: node.total_slots })}</span>
                    <span>
                      {t('llm.gpu.totalMemory', {
                        used: node.used_memory_gb.toFixed(1),
                        total: node.total_memory_gb.toFixed(0),
                      })}
                    </span>
                  </div>
                  {node.allocations.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {node.allocations.map((alloc) => (
                        <TkBadge key={alloc.model_id} status="healthy">
                          {alloc.model_id.split('/').pop()} ({alloc.estimated_memory_gb.toFixed(1)} GB)
                        </TkBadge>
                      ))}
                    </div>
                  )}
                </div>
              </TkCardContent>
            </TkCard>
          ))}
        </div>
      )}

      <TkCard className="mb-6">
        <TkCardHeader className="flex flex-row items-center justify-between">
          <TkCardTitle>{t('llm.backends.title')}</TkCardTitle>
          <TkButton intent="secondary" size="sm" onClick={handleRefresh} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            {t('llm.backends.refresh')}
          </TkButton>
        </TkCardHeader>
        <TkCardContent>
          {backends.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">{t('llm.backends.empty')}</div>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>{t('llm.backends.col.backend')}</TkTableHead>
                  <TkTableHead>{t('llm.backends.col.type')}</TkTableHead>
                  <TkTableHead>{t('llm.backends.col.status')}</TkTableHead>
                  <TkTableHead>{t('llm.backends.col.loadedModels')}</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {backends.map((backend) => (
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
                      <BackendStatusBadge t={t} status={backend.status} />
                    </TkTableCell>
                    <TkTableCell>
                      {backend.models.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {backend.models.map((m) => (
                            <TkBadge key={m} appearance="muted">
                              {m}
                            </TkBadge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">{t('llm.backends.none')}</span>
                      )}
                    </TkTableCell>
                  </TkTableRow>
                ))}
              </TkTableBody>
            </TkTable>
          )}
        </TkCardContent>
      </TkCard>

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('llm.models.title')}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent>
          {gatewayModels.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">{t('llm.models.empty')}</div>
          ) : (
            <TkTable>
              <TkTableHeader>
                <TkTableRow>
                  <TkTableHead>{t('llm.models.col.model')}</TkTableHead>
                  <TkTableHead>{t('llm.models.col.params')}</TkTableHead>
                  <TkTableHead>{t('llm.models.col.quantization')}</TkTableHead>
                  <TkTableHead>{t('llm.models.col.backendType')}</TkTableHead>
                  <TkTableHead>{t('llm.models.col.state')}</TkTableHead>
                  <TkTableHead>{t('llm.models.col.runningOn')}</TkTableHead>
                  <TkTableHead className="text-right">{t('llm.models.col.actions')}</TkTableHead>
                </TkTableRow>
              </TkTableHeader>
              <TkTableBody>
                {gatewayModels.map((model) => {
                  const context = formatContextValue(model.context_length);
                  return (
                    <TkTableRow key={model.id}>
                      <TkTableCell className="font-medium">
                        <div>
                          <div className="flex items-center gap-2 flex-wrap">
                            {model.name}
                            {model.is_finetuned && (
                              <TkBadge appearance="muted" className="text-xs">
                                {t('llm.badge.finetuned')}
                              </TkBadge>
                            )}
                            {model.reasoning_format && (
                              <TkBadge appearance="muted" className="text-xs">
                                {model.reasoning_format}
                              </TkBadge>
                            )}
                            {model.tool_use && (
                              <TkBadge appearance="muted" className="text-xs">
                                {t('llm.badge.tools')}
                              </TkBadge>
                            )}
                            {model.gated && (
                              <TkBadge status="warning" className="text-xs">
                                <Lock className="w-3 h-3 mr-0.5" />
                                {t('llm.badge.gated')}
                              </TkBadge>
                            )}
                          </div>
                          {model.description && (
                            <div className="text-sm text-muted-foreground">{model.description}</div>
                          )}
                          <div className="flex gap-2 text-xs text-muted-foreground mt-0.5">
                            {context && <span>{t('llm.context', { value: context })}</span>}
                            {model.license && <span>{model.license}</span>}
                          </div>
                        </div>
                      </TkTableCell>
                      <TkTableCell>
                        <div>
                          <div>
                            {model.params_b ? formatParams(t, model.params_b, model.active_params_b) : model.size || '-'}
                          </div>
                          {model.params_b && model.size && (
                            <div className="text-xs text-muted-foreground">{model.size}</div>
                          )}
                        </div>
                      </TkTableCell>
                      <TkTableCell>
                        {model.quantization ? <TkBadge appearance="outlined">{model.quantization}</TkBadge> : '-'}
                      </TkTableCell>
                      <TkTableCell>
                        <div className="flex gap-1 flex-wrap">
                          {model.server_type.map((type) => (
                            <TkBadge key={type} appearance="muted">
                              {type}
                            </TkBadge>
                          ))}
                        </div>
                      </TkTableCell>
                      <TkTableCell>
                        <ModelStateBadge t={t} state={model.state} />
                      </TkTableCell>
                      <TkTableCell>
                        {model.backend_id ? (
                          <TkBadge appearance="muted">
                            {/* A one-model backend's id ends in /<model>; the row already names the model */}
                            {model.backend_id.split('/')[0]}
                          </TkBadge>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TkTableCell>
                      <TkTableCell className="text-right">
                        <ModelActions
                          t={t}
                          model={model}
                          actionLoading={!!actionLoading[model.id]}
                          installedBackendTypes={installedBackendTypes}
                          onLoad={() => setLoadDialog(model)}
                          onUnload={() => handleUnload(model.id)}
                        />
                      </TkTableCell>
                    </TkTableRow>
                  );
                })}
              </TkTableBody>
            </TkTable>
          )}
        </TkCardContent>
      </TkCard>

      {loadDialog && (
        <LoadModelDialog
          model={loadDialog}
          backendTypes={backendTypes}
          onClose={() => setLoadDialog(null)}
          onLoaded={() => {
            setLoadDialog(null);
            fetchAll();
          }}
        />
      )}
    </TkPageWrapper>
  );
}

function MemoryBar({ label, detail, pct }: { label: string; detail: string; pct: number }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span>{detail}</span>
      </div>
      <div className="h-3 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor(pct)}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

/** One bar for a node's shared memory or its total GPU memory, or one per GPU when the node reports each. */
function GPUMemoryBars({ t, node }: { t: TFunction; node: GPUNode }) {
  const nodePct = node.total_memory_gb > 0 ? (node.used_memory_gb / node.total_memory_gb) * 100 : 0;
  const nodeDetail = `${node.used_memory_gb.toFixed(1)} / ${node.total_memory_gb.toFixed(0)} GB`;

  if (node.is_uma) {
    return <MemoryBar label={t('llm.gpu.sharedMemory')} detail={nodeDetail} pct={nodePct} />;
  }

  if (node.per_gpu_metrics.length > 0) {
    return (
      <div className="space-y-2">
        {node.per_gpu_metrics.map((gpu) => {
          const pct = gpu.memory_total_mb > 0 ? (gpu.memory_used_mb / gpu.memory_total_mb) * 100 : 0;
          let detail = `${(gpu.memory_used_mb / 1024).toFixed(1)} / ${(gpu.memory_total_mb / 1024).toFixed(0)} GB`;
          if (gpu.utilization > 0) detail += ` · ${gpu.utilization.toFixed(0)}%`;
          if (gpu.temp > 0) detail += ` · ${gpu.temp}°C`;
          return <MemoryBar key={gpu.index} label={t('llm.gpu.index', { index: gpu.index })} detail={detail} pct={pct} />;
        })}
      </div>
    );
  }

  return <MemoryBar label={t('llm.gpu.memory')} detail={nodeDetail} pct={nodePct} />;
}

function BackendStatusBadge({ t, status }: { t: TFunction; status: string }) {
  if (status === 'healthy') {
    return (
      <TkBadge status="healthy">
        <CheckCircle2 className="w-3 h-3 mr-1" />
        {t('llm.backends.status.healthy')}
      </TkBadge>
    );
  }
  if (['switching', 'starting', 'loading'].includes(status)) {
    return (
      <TkBadge status="active">
        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
        {t('llm.backends.status.provisioning')}
      </TkBadge>
    );
  }
  if (status === 'idle') {
    return <TkBadge appearance="muted">{t('llm.backends.status.idle')}</TkBadge>;
  }
  return <TkBadge status="warning">{t('llm.backends.status.unavailable')}</TkBadge>;
}

function ModelActions({
  t,
  model,
  actionLoading,
  installedBackendTypes,
  onLoad,
  onUnload,
}: {
  t: TFunction;
  model: ModelEntry;
  actionLoading: boolean;
  installedBackendTypes: string[];
  onLoad: () => void;
  onUnload: () => void;
}) {
  if (model.state === 'available') {
    return (
      <div className="flex gap-2 justify-end">
        <TkButton intent="secondary" size="sm" onClick={onUnload} disabled={actionLoading}>
          {actionLoading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Square className="w-4 h-4 mr-2" />}
          {t('llm.models.unload')}
        </TkButton>
      </div>
    );
  }

  if (model.state === 'loading') {
    return (
      <TkBadge status="active">
        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
        {t('llm.models.loadingAction')}
      </TkBadge>
    );
  }

  if (model.state === 'unloading') {
    return (
      <TkBadge status="pending">
        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
        {t('llm.models.unloadingAction')}
      </TkBadge>
    );
  }

  if (model.state === 'deployable' || model.state === 'registered') {
    if (!isModelLoadable(model.server_type)) {
      const label = nonLoadableLabel(model.server_type);
      return label ? <TkBadge appearance="muted">{t(label)}</TkBadge> : null;
    }

    const hasInstalledBackend = model.server_type.some((type) => installedBackendTypes.includes(type));
    if (!hasInstalledBackend) {
      return (
        <span className="text-xs text-muted-foreground max-w-[200px] text-right">
          {t('llm.models.requires', { types: gatewayTypes(model.server_type).join(', ') })}
        </span>
      );
    }

    return (
      <div className="flex flex-col items-end gap-1">
        <TkButton size="sm" onClick={onLoad} disabled={actionLoading}>
          {actionLoading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />}
          {t('llm.models.load')}
        </TkButton>
        {model.last_error && (
          <span className="text-xs text-destructive max-w-[200px] text-right">{model.last_error}</span>
        )}
      </div>
    );
  }

  return null;
}

function ModelStateBadge({ t, state }: { t: TFunction; state: string }) {
  switch (state) {
    case 'available':
      return (
        <TkBadge status="healthy">
          <CheckCircle2 className="w-3 h-3 mr-1" />
          {t('llm.state.available')}
        </TkBadge>
      );
    case 'loading':
      return (
        <TkBadge status="active">
          <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          {t('llm.state.loading')}
        </TkBadge>
      );
    case 'unloading':
      return (
        <TkBadge status="pending">
          <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          {t('llm.state.unloading')}
        </TkBadge>
      );
    case 'deployable':
      return <TkBadge status="pending">{t('llm.state.deployable')}</TkBadge>;
    case 'registered':
      return <TkBadge appearance="outlined">{t('llm.state.registered')}</TkBadge>;
    default:
      return <TkBadge appearance="outlined">{state}</TkBadge>;
  }
}
