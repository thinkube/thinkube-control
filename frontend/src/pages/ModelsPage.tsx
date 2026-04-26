import { useState, useEffect } from 'react';
import { Copy, Loader2, CheckCircle2, XCircle, Clock, ExternalLink, Trash2, Lock } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkTable, TkTableHeader, TkTableBody, TkTableRow, TkTableHead, TkTableCell } from 'thinkube-style/components/tables';
import { TkButton, TkLoadingButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { useModelDownloadsStore, Model, DownloadStatus } from '../stores/useModelDownloadsStore';
import api from '../lib/axios';

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

export default function ModelsPage() {
  const {
    models,
    downloads,
    loading,
    error,
    fetchModels,
    startDownload,
    fetchDownloads,
    isModelDownloading,
    getDownloadForModel,
    resetMirrorJob,
    deleteModel,
    startPolling,
    stopPolling,
  } = useModelDownloadsStore();

  const [mlflowStatus, setMlflowStatus] = useState<{
    initialized: boolean;
    needs_browser_login: boolean;
    mlflow_url: string;
    error?: string;
    message?: string;
  } | null>(null);
  const [checkingMlflow, setCheckingMlflow] = useState(true);

  useEffect(() => {
    // Check MLflow status on mount
    checkMlflowStatus();

    // Fetch models on mount
    fetchModels();
    fetchDownloads();

    // Start polling if there are active downloads
    startPolling();

    // Cleanup: stop polling on unmount
    return () => {
      stopPolling();
    };
  }, []);

  const checkMlflowStatus = async () => {
    // Construct MLflow URL from current domain
    const domainParts = window.location.hostname.split('.');
    const baseDomain = domainParts.slice(-2).join('.');
    const mlflowUrl = `https://mlflow.${baseDomain}`;

    try {
      const response = await api.get('/models/mlflow/status');
      setMlflowStatus(response.data);
    } catch (err: any) {
      console.error('Failed to check MLflow status:', err);
      // Use mlflow_url from error response if available, otherwise construct it
      const errorMlflowUrl = err.response?.data?.mlflow_url || mlflowUrl;
      const errorMessage = err.response?.data?.error || err.response?.data?.detail || 'Failed to check MLflow status';
      setMlflowStatus({
        initialized: false,
        needs_browser_login: true,
        mlflow_url: errorMlflowUrl,
        error: errorMessage,
      });
    } finally {
      setCheckingMlflow(false);
    }
  };

  const handleOpenMlflow = () => {
    if (mlflowStatus?.mlflow_url) {
      window.open(mlflowStatus.mlflow_url, '_blank');
      // After user logs in via browser, recheck status
      setTimeout(() => {
        checkMlflowStatus();
      }, 3000);
    }
  };

  const handleDownload = async (modelId: string) => {
    await startDownload(modelId);
  };

  const handleDelete = async (modelId: string) => {
    if (window.confirm('Are you sure you want to delete this model from MLflow? This will allow you to re-download it.')) {
      await deleteModel(modelId);
    }
  };

  const getModelStatus = (model: Model): {
    label: string;
    icon: React.ReactNode | null;
    status?: 'healthy' | 'unhealthy' | 'pending' | 'warning' | 'active';
    appearance?: 'prominent' | 'muted' | 'outlined';
  } => {
    const download = getDownloadForModel(model.id);

    // Fine-tuned models are always registered
    if (model.is_finetuned) {
      return {
        label: 'Registered',
        icon: <CheckCircle2 className="w-3 h-3" />,
        status: 'healthy',
      };
    }

    if (model.is_downloaded) {
      return {
        label: 'Mirrored',
        icon: <CheckCircle2 className="w-3 h-3" />,
        status: 'healthy',
      };
    }

    if (download) {
      if (download.is_running) {
        return {
          label: 'Mirroring',
          icon: <Loader2 className="w-3 h-3 animate-spin" />,
          status: 'active',
        };
      }

      if (download.is_complete) {
        return {
          label: 'Mirrored',
          icon: <CheckCircle2 className="w-3 h-3" />,
          status: 'healthy',
        };
      }

      if (download.is_failed) {
        return {
          label: 'Failed',
          icon: <XCircle className="w-3 h-3" />,
          status: 'unhealthy',
        };
      }

      // Workflow exists but not running/complete/failed = Pending
      return {
        label: 'Pending',
        icon: <Clock className="w-3 h-3" />,
        status: 'warning',
      };
    }

    // No workflow and not downloaded
    return {
      label: 'Not Mirrored',
      icon: null,
      appearance: 'outlined',
    };
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

  const activeDownloads = downloads.filter(d => d.is_running);

  return (
    <TkPageWrapper description="Mirror pre-optimized models from HuggingFace to MLflow model registry">

      {/* Error Alert */}
      {error && <TkErrorAlert title="Error" className="mb-6">{error}</TkErrorAlert>}

      {/* MLflow Initialization Banner */}
      {checkingMlflow ? (
        <TkInfoAlert
          title="Checking MLflow status..."
          className="mb-6"
        >
          Please wait while we verify MLflow initialization
        </TkInfoAlert>
      ) : mlflowStatus?.needs_browser_login && (
        <TkCard className="mb-6 border-blue-500 bg-blue-50 dark:bg-blue-950">
          <TkCardContent className="pt-6">
            <div className="flex items-start gap-4">
              <ExternalLink className="w-6 h-6 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-1" />
              <div className="flex-1">
                <h3 className="font-semibold text-lg mb-2">MLflow Initialization Required</h3>
                <p className="text-sm text-muted-foreground mb-4">
                  Before you can mirror AI models, you need to initialize MLflow by logging in through your browser.
                  This creates your user account in MLflow and enables seamless authentication.
                </p>
                {mlflowStatus.error && (
                  <p className="text-sm text-destructive mb-4">
                    Error: {mlflowStatus.error}
                  </p>
                )}
                <TkButton
                  size="sm"
                  onClick={handleOpenMlflow}
                >
                  <ExternalLink className="w-4 h-4 mr-2" />
                  Initialize MLflow
                </TkButton>
              </div>
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {/* Active Mirrors Summary */}
      {activeDownloads.length > 0 && (
        <TkInfoAlert
          title={`${activeDownloads.length} mirror operation(s) in progress`}
          className="mb-6"
        >
          Models are being mirrored in the background. The table will update automatically.
        </TkInfoAlert>
      )}

      {/* Models Table */}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>Available Models</TkCardTitle>
        </TkCardHeader>
        <TkCardContent>
          <TkTable>
            <TkTableHeader>
              <TkTableRow>
                <TkTableHead>Model</TkTableHead>
                <TkTableHead>Params</TkTableHead>
                <TkTableHead>Quantization</TkTableHead>
                <TkTableHead>Server Type</TkTableHead>
                <TkTableHead>Status</TkTableHead>
                <TkTableHead className="text-right">Actions</TkTableHead>
              </TkTableRow>
            </TkTableHeader>
            <TkTableBody>
              {models.map((model) => {
                const status = getModelStatus(model);
                const download = getDownloadForModel(model.id);
                const isDownloading = isModelDownloading(model.id);

                return (
                  <TkTableRow key={model.id}>
                    <TkTableCell className="font-medium">
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          {model.name}
                          {model.is_finetuned && (
                            <TkBadge appearance="muted" className="text-xs">
                              Fine-tuned
                            </TkBadge>
                          )}
                          {model.reasoning_format && (
                            <TkBadge appearance="muted" className="text-xs">
                              {model.reasoning_format}
                            </TkBadge>
                          )}
                          {model.tool_use && (
                            <TkBadge appearance="muted" className="text-xs">
                              tools
                            </TkBadge>
                          )}
                          {model.gated && (
                            <TkBadge status="warning" className="text-xs">
                              <Lock className="w-3 h-3 mr-0.5" />
                              gated
                            </TkBadge>
                          )}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {model.description}
                        </div>
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
                      <TkBadge appearance="outlined">{model.quantization}</TkBadge>
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
                      <TkBadge status={status.status} appearance={status.appearance}>
                        <span className="flex items-center gap-1">
                          {status.icon}
                          {status.label}
                        </span>
                      </TkBadge>
                    </TkTableCell>
                    <TkTableCell className="text-right">
                      <div className="flex gap-2 justify-end">
                        {/* Fine-tuned models are always ready - no Mirror button */}
                        {model.is_finetuned ? (
                          <>
                            <TkBadge status="healthy">
                              <CheckCircle2 className="w-3 h-3 mr-1" />
                              Ready
                            </TkBadge>
                            <TkButton
                              intent="secondary"
                              size="sm"
                              onClick={() => handleDelete(model.id)}
                            >
                              <Trash2 className="w-4 h-4 mr-2" />
                              Delete
                            </TkButton>
                          </>
                        ) : model.is_downloaded && download && !download.is_failed ? (
                          <>
                            <TkBadge status="healthy">
                              <CheckCircle2 className="w-3 h-3 mr-1" />
                              Ready
                            </TkBadge>
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => resetMirrorJob(model.id)}
                            >
                              Reset
                            </TkButton>
                            <TkButton
                              intent="secondary"
                              size="sm"
                              onClick={() => handleDelete(model.id)}
                            >
                              <Trash2 className="w-4 h-4 mr-2" />
                              Delete
                            </TkButton>
                          </>
                        ) : isDownloading && download?.workflow_name ? (
                          <>
                            <TkButton
                              intent="secondary"
                              size="sm"
                              asChild
                            >
                              <a
                                href={`https://argo.${window.location.hostname.split('.').slice(-2).join('.')}/workflows/argo/${download.workflow_name}`}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                <ExternalLink className="w-4 h-4 mr-2" />
                                Monitor
                              </a>
                            </TkButton>
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => resetMirrorJob(model.id)}
                            >
                              Reset
                            </TkButton>
                          </>
                        ) : download?.is_failed || (model.is_downloaded && download?.is_failed) ? (
                          <>
                            <TkButton
                              intent="secondary"
                              size="sm"
                              onClick={() => handleDownload(model.id)}
                              disabled={!mlflowStatus?.initialized}
                            >
                              <Copy className="w-4 h-4 mr-2" />
                              Retry
                            </TkButton>
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => resetMirrorJob(model.id)}
                            >
                              Reset
                            </TkButton>
                            <TkButton
                              intent="danger"
                              size="sm"
                              onClick={() => handleDelete(model.id)}
                            >
                              <Trash2 className="w-4 h-4 mr-2" />
                              Delete
                            </TkButton>
                          </>
                        ) : download ? (
                          <>
                            <TkButton
                              intent="secondary"
                              size="sm"
                              onClick={() => handleDownload(model.id)}
                              disabled={!mlflowStatus?.initialized}
                            >
                              <Copy className="w-4 h-4 mr-2" />
                              Mirror
                            </TkButton>
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => resetMirrorJob(model.id)}
                            >
                              Reset
                            </TkButton>
                          </>
                        ) : (
                          <TkButton
                            size="sm"
                            onClick={() => handleDownload(model.id)}
                            disabled={loading || !mlflowStatus?.initialized}
                          >
                            <Copy className="w-4 h-4 mr-2" />
                            Mirror
                          </TkButton>
                        )}
                      </div>
                    </TkTableCell>
                  </TkTableRow>
                );
              })}
            </TkTableBody>
          </TkTable>

          {models.length === 0 && !loading && (
            <div className="text-center py-12 text-muted-foreground">
              No models available
            </div>
          )}
        </TkCardContent>
      </TkCard>
    </TkPageWrapper>
  );
}
