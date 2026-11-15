/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import {
  TkLabel,
  TkSelect,
  TkSelectTrigger,
  TkSelectValue,
  TkSelectContent,
  TkSelectItem,
} from 'thinkube-style/components/forms-inputs';
import { Loader2, Zap } from 'lucide-react';
import api from '@/lib/axios';
import { errorText } from '@/lib/errorText';
import {
  formatContextValue,
  formatGpuProduct,
  formatParams,
  barColor,
  type BackendType,
  type GPUNode,
  type LoadOptions,
  type ModelEntry,
  type TFunction,
} from './types';

const CONTEXT_OPTIONS = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288];

/** The context offered first: the model's own when it is smaller. */
const DEFAULT_CONTEXT = 8192;
/** From this context on, the dialog warns about the memory of the KV cache. */
const LARGE_CONTEXT_THRESHOLD = 32768;
/** A context selector is offered only for a model whose context is larger than this. */
const MIN_SELECTABLE_CONTEXT = 4096;
/** The share of a GPU's memory a tensor-parallel shard is planned to fill. */
const GPU_FILL = 0.85;

interface Props {
  model: ModelEntry;
  backendTypes: BackendType[];
  onClose: () => void;
  onLoaded: () => void;
}

function nodeDescription(t: TFunction, node: GPUNode): string {
  const count = node.gpu_count > 1 ? `${node.gpu_count}x ` : '';
  const memory =
    node.gpu_count > 1
      ? t('llm.dialog.memoryEach', { value: node.per_gpu_memory_gb.toFixed(0) })
      : t('llm.dialog.memory', { value: node.per_gpu_memory_gb.toFixed(0) });
  return `${count}${formatGpuProduct(t, node.gpu_product)} (${memory})`;
}

/** Loads a model on a GPU node with POST /llm/models/{id}/load. Mount it only while a model is being loaded. */
export default function LoadModelDialog({ model, backendTypes, onClose, onLoaded }: Props) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<LoadOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [selectedNode, setSelectedNode] = useState('');
  const [selectedContext, setSelectedContext] = useState('');
  const [specTokens, setSpecTokens] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The model uses speculative decoding when its catalogue config sets a method
  const usesSpeculative = !!model.speculative_config;

  // The model's backend types that are installed, in the model's order
  const compatibleTypes = backendTypes.filter((b) => model.server_type.includes(b.type));
  compatibleTypes.sort((a, b) => model.server_type.indexOf(a.type) - model.server_type.indexOf(b.type));
  const [selectedBackendType, setSelectedBackendType] = useState(compatibleTypes[0]?.type ?? '');

  useEffect(() => {
    // The model's id holds a slash, which the API's path takes as it is
    api
      .get(`/llm/models/${model.id}/load-options`)
      .then((res) => {
        const opts: LoadOptions = res.data;
        setOptions(opts);
        if (opts.gpu_nodes.length > 0) setSelectedNode(opts.gpu_nodes[0].name);
        if (opts.context_length) setSelectedContext(String(Math.min(opts.context_length, DEFAULT_CONTEXT)));
      })
      .catch((err) => setError(errorText(err)))
      .finally(() => setLoadingOptions(false));
  }, [model.id]);

  const handleLoad = async () => {
    setLoading(true);
    setError(null);
    try {
      const maxCtx = selectedContext ? parseInt(selectedContext, 10) : undefined;
      const specK = usesSpeculative && specTokens ? parseInt(specTokens, 10) : undefined;
      const resp = await api.post(`/llm/models/${model.id}/load`, {
        backend: selectedBackendType || undefined,
        node: selectedNode || undefined,
        max_context_length: maxCtx || undefined,
        num_speculative_tokens: specK || undefined,
      });
      // The API answers 200 when it refuses a load too; its message says why
      if (resp.data.state !== 'available' && resp.data.state !== 'loading') {
        setError(resp.data.message);
      } else {
        onLoaded();
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  const selectedNodeData = options?.gpu_nodes.find((n) => n.name === selectedNode);
  const maxContext = options?.context_length ?? null;
  const contextValue = selectedContext ? parseInt(selectedContext, 10) : null;
  const params = formatParams(t, model.params_b, model.active_params_b);
  const context = formatContextValue(model.context_length);

  return (
    <TkDialogRoot open onOpenChange={(open: boolean) => !open && onClose()}>
      <TkDialogContent className="max-w-lg">
        <TkDialogHeader>
          <TkDialogTitle>{t('llm.dialog.title')}</TkDialogTitle>
        </TkDialogHeader>

        {loadingOptions ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin" />
          </div>
        ) : options ? (
          <div className="space-y-5">
            <div className="rounded-lg border p-3 space-y-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium">{model.name}</span>
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
              </div>
              <div className="flex gap-2 text-sm text-muted-foreground flex-wrap">
                {params ? <span>{params}</span> : model.size ? <span>{model.size}</span> : null}
                {model.quantization && <TkBadge appearance="outlined">{model.quantization}</TkBadge>}
                {context && <span>{t('llm.dialog.tokens', { value: context })}</span>}
              </div>
              <div className="flex items-center gap-1 text-sm">
                <Zap className="w-3.5 h-3.5" />
                <span>{t('llm.dialog.estimatedMemory', { value: options.estimated_memory_gb.toFixed(1) })}</span>
              </div>
            </div>

            <div className="space-y-2">
              <TkLabel>{t('llm.dialog.targetNode')}</TkLabel>
              {options.gpu_nodes.length === 0 ? (
                <div className="text-sm text-muted-foreground">{t('llm.dialog.noNodes')}</div>
              ) : (
                <TkSelect value={selectedNode} onValueChange={setSelectedNode}>
                  <TkSelectTrigger>
                    <TkSelectValue placeholder={t('llm.dialog.selectNode')} />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {options.gpu_nodes.map((n) => (
                      <TkSelectItem key={n.name} value={n.name}>
                        {n.name} — {nodeDescription(t, n)}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              )}
            </div>

            {compatibleTypes.length > 1 && (
              <div className="space-y-2">
                <TkLabel>{t('llm.dialog.backendType')}</TkLabel>
                <TkSelect value={selectedBackendType} onValueChange={setSelectedBackendType}>
                  <TkSelectTrigger>
                    <TkSelectValue placeholder={t('llm.dialog.selectBackendType')} />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {compatibleTypes.map((b) => (
                      <TkSelectItem key={b.type} value={b.type}>
                        {b.display_name}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              </div>
            )}

            {maxContext !== null && maxContext > MIN_SELECTABLE_CONTEXT && (
              <div className="space-y-2">
                <TkLabel>{t('llm.dialog.contextLength')}</TkLabel>
                <TkSelect value={selectedContext} onValueChange={setSelectedContext}>
                  <TkSelectTrigger>
                    <TkSelectValue placeholder={t('llm.dialog.selectContext')} />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {CONTEXT_OPTIONS.filter((value) => value <= maxContext).map((value) => (
                      <TkSelectItem key={value} value={String(value)}>
                        {value === maxContext
                          ? t('llm.dialog.tokensMax', { value: formatContextValue(value) })
                          : t('llm.dialog.tokens', { value: formatContextValue(value) })}
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
                {contextValue !== null && contextValue >= LARGE_CONTEXT_THRESHOLD && (
                  <p className="text-xs font-medium border-l-4 border-warning pl-2">{t('llm.dialog.largeContext')}</p>
                )}
                {contextValue !== null && contextValue < maxContext && contextValue < LARGE_CONTEXT_THRESHOLD && (
                  <p className="text-xs text-muted-foreground">{t('llm.dialog.reducedContext')}</p>
                )}
              </div>
            )}

            {usesSpeculative && (
              <div className="space-y-2">
                <TkLabel htmlFor="spec-tokens">{t('llm.dialog.specTokens')}</TkLabel>
                <input
                  id="spec-tokens"
                  type="number"
                  min={1}
                  max={32}
                  value={specTokens}
                  onChange={(e) => setSpecTokens(e.target.value)}
                  placeholder={t('llm.dialog.specTokensPlaceholder')}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
                <p className="text-xs text-muted-foreground">{t('llm.dialog.specTokensHelp')}</p>
              </div>
            )}

            {selectedNodeData && (
              <NodeImpact t={t} node={selectedNodeData} estimatedMem={options.estimated_memory_gb} />
            )}

            {error && <div className="text-sm text-destructive">{error}</div>}
          </div>
        ) : error ? (
          <div className="text-sm text-destructive py-4">{error}</div>
        ) : null}

        <TkDialogFooter>
          <TkButton intent="secondary" onClick={onClose} disabled={loading}>
            {t('common.cancel')}
          </TkButton>
          <TkButton
            onClick={handleLoad}
            disabled={loading || loadingOptions || !options || !selectedNode || !selectedBackendType}
          >
            {loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
            {t('llm.dialog.load')}
          </TkButton>
        </TkDialogFooter>
      </TkDialogContent>
    </TkDialogRoot>
  );
}

/** What the load does to the chosen node: its memory with the model added, and the GPUs it needs. */
function NodeImpact({ t, node, estimatedMem }: { t: TFunction; node: GPUNode; estimatedMem: number }) {
  const gpusNeeded =
    node.per_gpu_memory_gb > 0 && !node.shared_memory ? Math.ceil(estimatedMem / (node.per_gpu_memory_gb * GPU_FILL)) : 1;

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div>
        <div className="font-medium">{node.name}</div>
        <div className="text-sm text-muted-foreground">
          {nodeDescription(t, node)}
          {node.shared_memory ? ` — ${t('llm.gpu.timeSliced')}` : ''}
        </div>
      </div>
      {node.is_uma || node.per_gpu_metrics.length === 0 ? (
        <LoadPreviewBar
          label={node.is_uma ? t('llm.gpu.sharedMemory') : t('llm.gpu.memory')}
          usedGb={node.used_memory_gb}
          totalGb={node.total_memory_gb}
          modelGb={estimatedMem}
        />
      ) : (
        node.per_gpu_metrics.map((gpu) => (
          <LoadPreviewBar
            key={gpu.index}
            label={t('llm.gpu.index', { index: gpu.index })}
            usedGb={gpu.memory_used_mb / 1024}
            totalGb={gpu.memory_total_mb / 1024}
          />
        ))
      )}
      {!node.shared_memory && gpusNeeded > 1 && (
        <div className="text-sm border-l-4 border-warning pl-2">{t('llm.dialog.requiresGpus', { count: gpusNeeded })}</div>
      )}
      {!node.shared_memory && gpusNeeded > node.available_slots && (
        <div className="text-sm text-destructive">{t('llm.dialog.notEnoughGpus', { count: node.available_slots })}</div>
      )}
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>{t('llm.dialog.modelMemory', { value: estimatedMem.toFixed(1) })}</span>
        <span>{t('llm.dialog.gpusAvailable', { available: node.available_slots, total: node.total_slots })}</span>
      </div>
    </div>
  );
}

/** A memory bar; with modelGb, a lighter bar behind it shows the use once the model is loaded. */
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
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span>
          {usedGb.toFixed(1)} / {totalGb.toFixed(0)} GB
        </span>
      </div>
      <div className="h-2.5 bg-muted rounded-full overflow-hidden relative">
        {modelGb != null && projPct > 0 && (
          <div className="absolute h-full rounded-full bg-blue-500/30" style={{ width: `${Math.min(projPct, 100)}%` }} />
        )}
        <div
          className={`relative h-full rounded-full transition-all duration-500 ${barColor(pct)}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}
