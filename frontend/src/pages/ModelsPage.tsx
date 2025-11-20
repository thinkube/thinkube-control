import { useState, useEffect } from 'react';
import { Copy, Loader2, CheckCircle2, XCircle, Clock, ExternalLink, Trash2 } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkTable, TkTableHeader, TkTableBody, TkTableRow, TkTableHead, TkTableCell } from 'thinkube-style/components/tables';
import { TkButton, TkLoadingButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { useModelDownloadsStore, Model, DownloadStatus } from '../stores/useModelDownloadsStore';
import api from '../lib/axios';

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
    try {
      const response = await api.get('/models/mlflow/status');
      setMlflowStatus(response.data);
    } catch (err: any) {
      console.error('Failed to check MLflow status:', err);
      setMlflowStatus({
        initialized: false,
        needs_browser_login: true,
        mlflow_url: '',
        error: err.response?.data?.detail || 'Failed to check MLflow status',
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
    variant: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning';
  } => {
    const download = getDownloadForModel(model.id);

    if (model.is_downloaded) {
      return {
        label: 'Mirrored',
        icon: <CheckCircle2 className="w-3 h-3" />,
        variant: 'success',
      };
    }

    if (download) {
      if (download.is_running) {
        return {
          label: 'Mirroring',
          icon: <Loader2 className="w-3 h-3 animate-spin" />,
          variant: 'default',
        };
      }

      if (download.is_complete) {
        return {
          label: 'Mirrored',
          icon: <CheckCircle2 className="w-3 h-3" />,
          variant: 'success',
        };
      }

      if (download.is_failed) {
        return {
          label: 'Failed',
          icon: <XCircle className="w-3 h-3" />,
          variant: 'destructive',
        };
      }

      // Workflow exists but not running/complete/failed = Pending
      return {
        label: 'Pending',
        icon: <Clock className="w-3 h-3" />,
        variant: 'warning',
      };
    }

    // No workflow and not downloaded
    return {
      label: 'Not Mirrored',
      icon: null,
      variant: 'outline',
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
    <TkPageWrapper>
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold">AI Models</h1>
        <p className="text-muted-foreground mt-2">
          Mirror pre-optimized models from HuggingFace to MLflow model registry
        </p>
      </div>

      {/* Error Alert */}
      {error && <TkErrorAlert title="Error" message={error} className="mb-6" />}

      {/* MLflow Initialization Banner */}
      {checkingMlflow ? (
        <TkInfoAlert
          title="Checking MLflow status..."
          message="Please wait while we verify MLflow initialization"
          className="mb-6"
        />
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
                  variant="default"
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
          message="Models are being mirrored in the background. The table will update automatically."
          className="mb-6"
        />
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
                <TkTableHead>Size</TkTableHead>
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
                        <div>{model.name}</div>
                        <div className="text-sm text-muted-foreground">
                          {model.description}
                        </div>
                      </div>
                    </TkTableCell>
                    <TkTableCell>{model.size}</TkTableCell>
                    <TkTableCell>
                      <TkBadge variant="outline">{model.quantization}</TkBadge>
                    </TkTableCell>
                    <TkTableCell>
                      <div className="flex gap-1 flex-wrap">
                        {model.server_type.map((type) => (
                          <TkBadge key={type} variant="secondary">
                            {type}
                          </TkBadge>
                        ))}
                      </div>
                    </TkTableCell>
                    <TkTableCell>
                      <TkBadge variant={status.variant}>
                        <span className="flex items-center gap-1">
                          {status.icon}
                          {status.label}
                        </span>
                      </TkBadge>
                    </TkTableCell>
                    <TkTableCell className="text-right">
                      {model.is_downloaded && !download?.is_failed ? (
                        <div className="flex gap-2 justify-end">
                          <TkBadge variant="success">
                            <CheckCircle2 className="w-3 h-3 mr-1" />
                            Ready
                          </TkBadge>
                          <TkButton
                            variant="outline"
                            size="sm"
                            onClick={() => handleDelete(model.id)}
                          >
                            <Trash2 className="w-4 h-4 mr-2" />
                            Delete
                          </TkButton>
                        </div>
                      ) : isDownloading && download?.workflow_name ? (
                        <TkButton
                          variant="outline"
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
                      ) : download?.is_failed || (model.is_downloaded && download?.is_failed) ? (
                        <div className="flex gap-2 justify-end">
                          <TkButton
                            variant="outline"
                            size="sm"
                            onClick={() => handleDownload(model.id)}
                          >
                            <Copy className="w-4 h-4 mr-2" />
                            Retry
                          </TkButton>
                          <TkButton
                            variant="destructive"
                            size="sm"
                            onClick={() => handleDelete(model.id)}
                          >
                            <Trash2 className="w-4 h-4 mr-2" />
                            Delete
                          </TkButton>
                        </div>
                      ) : (
                        <TkButton
                          variant="default"
                          size="sm"
                          onClick={() => handleDownload(model.id)}
                          disabled={loading}
                        >
                          <Copy className="w-4 h-4 mr-2" />
                          Mirror
                        </TkButton>
                      )}
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
