/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, Loader2, CheckCircle2, XCircle, Clock, ExternalLink, Trash2, Lock } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkTable, TkTableHeader, TkTableBody, TkTableRow, TkTableHead, TkTableCell } from 'thinkube-style/components/tables';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import api from '@/lib/axios';
import { useModelsStore, errorText, type Model, type MirrorJob } from '@/stores/useModelsStore';
import { ConfirmDialog } from '@/components/models/ConfirmDialog';

type TFunction = ReturnType<typeof useTranslation>['t'];

interface MlflowStatus {
  initialized: boolean;
  needs_browser_login: boolean;
  mlflow_url: string;
  error?: string;
  message?: string;
}

interface ModelStatus {
  label: string;
  icon: ReactNode | null;
  status?: 'healthy' | 'unhealthy' | 'pending' | 'warning' | 'active';
  appearance?: 'prominent' | 'muted' | 'outlined';
}

// How long after MLflow is opened in a new tab its status is read again
const MLFLOW_RECHECK_MS = 3000;

function formatCount(billions: number): string {
  return billions >= 1 ? `${billions}B` : `${(billions * 1000).toFixed(0)}M`;
}

function formatParams(t: TFunction, params_b: number | null, active_params_b: number | null): string {
  if (!params_b) return '-';
  const main = formatCount(params_b);
  if (active_params_b) return t('models.activeParams', { main, active: formatCount(active_params_b) });
  return main;
}

function formatContextLength(t: TFunction, ctx: number | null): string {
  if (!ctx) return '';
  if (ctx >= 1000000) return t('models.context', { value: `${(ctx / 1000000).toFixed(0)}M` });
  if (ctx >= 1000) return t('models.context', { value: `${Math.round(ctx / 1000)}K` });
  return t('models.context', { value: String(ctx) });
}

function modelStatus(t: TFunction, model: Model, mirror: MirrorJob | undefined): ModelStatus {
  if (model.is_finetuned) {
    return { label: t('models.status.registered'), icon: <CheckCircle2 className="w-3 h-3" />, status: 'healthy' };
  }
  if (model.is_downloaded) {
    return { label: t('models.status.mirrored'), icon: <CheckCircle2 className="w-3 h-3" />, status: 'healthy' };
  }
  if (mirror) {
    if (mirror.is_running) {
      return { label: t('models.status.mirroring'), icon: <Loader2 className="w-3 h-3 animate-spin" />, status: 'active' };
    }
    if (mirror.is_complete) {
      return { label: t('models.status.mirrored'), icon: <CheckCircle2 className="w-3 h-3" />, status: 'healthy' };
    }
    if (mirror.is_failed) {
      return { label: t('models.status.failed'), icon: <XCircle className="w-3 h-3" />, status: 'unhealthy' };
    }
    return { label: t('models.status.pending'), icon: <Clock className="w-3 h-3" />, status: 'warning' };
  }
  return { label: t('models.status.notMirrored'), icon: null, appearance: 'outlined' };
}

/** The model catalogue: each model's mirror into MLflow (Experiments), started, followed, reset and deleted here. */
export default function ModelsPage() {
  const { t } = useTranslation();
  const {
    models,
    mirrors,
    loading,
    error,
    mirrorsError,
    fetchModels,
    startMirror,
    fetchMirrors,
    isModelMirroring,
    getMirrorForModel,
    resetMirrorJob,
    deleteModel,
    startPolling,
    stopPolling,
  } = useModelsStore();

  const [mlflowStatus, setMlflowStatus] = useState<MlflowStatus | null>(null);
  const [mlflowError, setMlflowError] = useState<string | null>(null);
  const [checkingMlflow, setCheckingMlflow] = useState(true);
  const [deleting, setDeleting] = useState<Model | null>(null);
  const [working, setWorking] = useState(false);
  const recheckTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const checkMlflowStatus = async () => {
    try {
      const { data } = await api.get('/models/mlflow/status');
      setMlflowStatus(data);
      setMlflowError(null);
    } catch (err) {
      setMlflowStatus(null);
      setMlflowError(errorText(err));
    } finally {
      setCheckingMlflow(false);
    }
  };

  useEffect(() => {
    checkMlflowStatus();
    fetchModels();
    fetchMirrors();
    // The first reading of the mirror jobs stops the polling when none of them runs
    startPolling();
    return () => {
      stopPolling();
      if (recheckTimer.current) clearTimeout(recheckTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleOpenMlflow = () => {
    if (!mlflowStatus?.mlflow_url) return;
    window.open(mlflowStatus.mlflow_url, '_blank');
    // The user signs in on the new tab; the status is read again after that
    recheckTimer.current = setTimeout(checkMlflowStatus, MLFLOW_RECHECK_MS);
  };

  const handleMirror = (modelId: string) => startMirror(modelId);

  const confirmDelete = async () => {
    if (!deleting) return;
    setWorking(true);
    await deleteModel(deleting.id);
    setWorking(false);
    setDeleting(null);
  };

  if (loading && models.length === 0) {
    return (
      <TkPageWrapper>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin" />
        </div>
      </TkPageWrapper>
    );
  }

  const activeMirrors = mirrors.filter((job) => job.is_running);
  const canMirror = !!mlflowStatus?.initialized;

  const resetButton = (modelId: string) => (
    <TkButton intent="ghost" size="sm" onClick={() => resetMirrorJob(modelId)}>
      {t('models.reset')}
    </TkButton>
  );

  const deleteButton = (model: Model, intent: 'secondary' | 'danger') => (
    <TkButton intent={intent} size="sm" onClick={() => setDeleting(model)}>
      <Trash2 className="w-4 h-4 mr-2" />
      {t('models.delete')}
    </TkButton>
  );

  const readyBadge = (
    <TkBadge status="healthy">
      <CheckCircle2 className="w-3 h-3 mr-1" />
      {t('models.ready')}
    </TkBadge>
  );

  const actions = (model: Model, mirror: MirrorJob | undefined) => {
    if (model.is_finetuned) {
      return (
        <>
          {readyBadge}
          {deleteButton(model, 'secondary')}
        </>
      );
    }
    if (model.is_downloaded && mirror && !mirror.is_failed) {
      return (
        <>
          {readyBadge}
          {resetButton(model.id)}
          {deleteButton(model, 'secondary')}
        </>
      );
    }
    if (isModelMirroring(model.id) && mirror?.workflow_url) {
      return (
        <>
          <TkButton intent="secondary" size="sm" asChild>
            <a href={mirror.workflow_url} target="_blank" rel="noopener noreferrer">
              <ExternalLink className="w-4 h-4 mr-2" />
              {t('models.monitor')}
            </a>
          </TkButton>
          {resetButton(model.id)}
        </>
      );
    }
    if (mirror?.is_failed) {
      return (
        <>
          <TkButton intent="secondary" size="sm" onClick={() => handleMirror(model.id)} disabled={!canMirror}>
            <Copy className="w-4 h-4 mr-2" />
            {t('models.retry')}
          </TkButton>
          {resetButton(model.id)}
          {deleteButton(model, 'danger')}
        </>
      );
    }
    if (mirror) {
      return (
        <>
          <TkButton intent="secondary" size="sm" onClick={() => handleMirror(model.id)} disabled={!canMirror}>
            <Copy className="w-4 h-4 mr-2" />
            {t('models.mirror')}
          </TkButton>
          {resetButton(model.id)}
        </>
      );
    }
    return (
      <TkButton size="sm" onClick={() => handleMirror(model.id)} disabled={loading || !canMirror}>
        <Copy className="w-4 h-4 mr-2" />
        {t('models.mirror')}
      </TkButton>
    );
  };

  return (
    <TkPageWrapper description={t('models.description')}>
      {error && (
        <TkErrorAlert title={t('models.errorTitle')} className="mb-6">
          {error}
        </TkErrorAlert>
      )}

      {mirrorsError && (
        <TkErrorAlert title={t('models.mirrorsReadFailed')} className="mb-6">
          {mirrorsError}
        </TkErrorAlert>
      )}

      {mlflowError && (
        <TkErrorAlert title={t('models.mlflowStatusFailed')} className="mb-6">
          {mlflowError}
        </TkErrorAlert>
      )}

      {checkingMlflow ? (
        <TkInfoAlert title={t('models.checkingMlflow')} className="mb-6">
          {t('models.checkingMlflowMessage')}
        </TkInfoAlert>
      ) : (
        mlflowStatus?.needs_browser_login && (
          <TkCard className="mb-6 border-blue-500 bg-blue-50 dark:bg-blue-950">
            <TkCardContent className="pt-6">
              <div className="flex items-start gap-4">
                <ExternalLink className="w-6 h-6 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-1" />
                <div className="flex-1">
                  <h3 className="font-semibold text-lg mb-2">{t('models.init.title')}</h3>
                  <p className="text-sm text-muted-foreground mb-4">{t('models.init.message')}</p>
                  {mlflowStatus.error && (
                    <p className="text-sm text-destructive mb-4">{t('models.init.error', { error: mlflowStatus.error })}</p>
                  )}
                  <TkButton size="sm" onClick={handleOpenMlflow}>
                    <ExternalLink className="w-4 h-4 mr-2" />
                    {t('models.init.button')}
                  </TkButton>
                </div>
              </div>
            </TkCardContent>
          </TkCard>
        )
      )}

      {activeMirrors.length > 0 && (
        <TkInfoAlert title={t('models.active.title', { count: activeMirrors.length })} className="mb-6">
          {t('models.active.message')}
        </TkInfoAlert>
      )}

      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{t('models.tableTitle')}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent>
          <TkTable>
            <TkTableHeader>
              <TkTableRow>
                <TkTableHead>{t('models.col.model')}</TkTableHead>
                <TkTableHead>{t('models.col.params')}</TkTableHead>
                <TkTableHead className="hidden xl:table-cell">{t('models.col.quantization')}</TkTableHead>
                <TkTableHead className="hidden xl:table-cell">{t('models.col.serverType')}</TkTableHead>
                <TkTableHead>{t('models.col.status')}</TkTableHead>
                <TkTableHead className="text-right">{t('models.col.actions')}</TkTableHead>
              </TkTableRow>
            </TkTableHeader>
            <TkTableBody>
              {models.map((model) => {
                const mirror = getMirrorForModel(model.id);
                const status = modelStatus(t, model, mirror);
                const context = formatContextLength(t, model.context_length);

                return (
                  <TkTableRow key={model.id}>
                    <TkTableCell className="font-medium">
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          {model.name}
                          {model.is_finetuned && (
                            <TkBadge appearance="muted" className="text-xs">
                              {t('models.badge.finetuned')}
                            </TkBadge>
                          )}
                          {model.reasoning_format && (
                            <TkBadge appearance="muted" className="text-xs">
                              {model.reasoning_format}
                            </TkBadge>
                          )}
                          {model.tool_use && (
                            <TkBadge appearance="muted" className="text-xs">
                              {t('models.badge.tools')}
                            </TkBadge>
                          )}
                          {model.gated && (
                            <TkBadge status="warning" className="text-xs">
                              <Lock className="w-3 h-3 mr-0.5" />
                              {t('models.badge.gated')}
                            </TkBadge>
                          )}
                        </div>
                        <div className="text-sm text-muted-foreground">{model.description}</div>
                        <div className="flex gap-2 text-xs text-muted-foreground mt-0.5">
                          {context && <span>{context}</span>}
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
                    <TkTableCell className="hidden xl:table-cell">
                      <TkBadge appearance="outlined">{model.quantization}</TkBadge>
                    </TkTableCell>
                    <TkTableCell className="hidden xl:table-cell">
                      <div className="flex gap-1 flex-wrap">
                        {model.server_type.map((type) => (
                          <TkBadge key={type} appearance="muted">
                            {type}
                          </TkBadge>
                        ))}
                      </div>
                    </TkTableCell>
                    <TkTableCell>
                      <TkBadge status={status.status} appearance={status.appearance}>
                        <span className="flex items-center gap-1">
                          {status.icon}
                          {status.label}
                        </span>
                      </TkBadge>
                      {!model.is_downloaded && mirror?.is_failed && mirror.error_message && (
                        <div className="text-xs text-destructive mt-1 max-w-xs break-words">{mirror.error_message}</div>
                      )}
                    </TkTableCell>
                    <TkTableCell className="text-right">
                      <div className="flex gap-2 justify-end">{actions(model, mirror)}</div>
                    </TkTableCell>
                  </TkTableRow>
                );
              })}
            </TkTableBody>
          </TkTable>

          {models.length === 0 && !loading && (
            <div className="text-center py-12 text-muted-foreground">{t('models.empty')}</div>
          )}
        </TkCardContent>
      </TkCard>

      {deleting && (
        <ConfirmDialog
          title={t('models.deleteTitle')}
          message={t('models.deleteConfirm', { name: deleting.name })}
          confirmLabel={t('models.delete')}
          danger
          working={working}
          onConfirm={confirmDelete}
          onCancel={() => setDeleting(null)}
        />
      )}
    </TkPageWrapper>
  );
}
