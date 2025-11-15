import { useState, useEffect } from 'react';
import { Copy, Loader2, CheckCircle2, XCircle, Clock, ExternalLink } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkTable, TkTableHeader, TkTableBody, TkTableRow, TkTableHead, TkTableCell } from 'thinkube-style/components/tables';
import { TkButton, TkLoadingButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { useModelDownloadsStore, Model, DownloadStatus } from '../stores/useModelDownloadsStore';

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
    startPolling,
    stopPolling,
  } = useModelDownloadsStore();

  useEffect(() => {
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

  const handleDownload = async (modelId: string) => {
    await startDownload(modelId);
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
                      {model.is_downloaded ? (
                        <TkBadge variant="success">
                          <CheckCircle2 className="w-3 h-3 mr-1" />
                          Ready
                        </TkBadge>
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
                      ) : download?.is_failed ? (
                        <TkButton
                          variant="outline"
                          size="sm"
                          onClick={() => handleDownload(model.id)}
                        >
                          <Copy className="w-4 h-4 mr-2" />
                          Retry
                        </TkButton>
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
