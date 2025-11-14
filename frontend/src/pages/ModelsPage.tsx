import { useState, useEffect } from 'react';
import { Download, Loader2, CheckCircle2, XCircle, Clock } from 'lucide-react';
import { TkErrorAlert } from 'thinkube-style/components/feedback';
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
    icon: React.ReactNode;
    className: string;
  } => {
    const download = getDownloadForModel(model.id);

    // Check if model is already downloaded on the PVC (priority check)
    if (model.is_downloaded && !download?.is_running) {
      return {
        label: 'Downloaded',
        icon: <CheckCircle2 className="w-4 h-4" />,
        className: 'text-green-500',
      };
    }

    // Check workflow status
    if (download) {
      if (download.is_running) {
        return {
          label: 'Downloading',
          icon: <Loader2 className="w-4 h-4 animate-spin" />,
          className: 'text-blue-500',
        };
      }

      if (download.is_complete) {
        return {
          label: 'Downloaded',
          icon: <CheckCircle2 className="w-4 h-4" />,
          className: 'text-green-500',
        };
      }

      if (download.is_failed) {
        return {
          label: 'Failed',
          icon: <XCircle className="w-4 h-4" />,
          className: 'text-red-500',
        };
      }

      // Workflow exists but not running/complete/failed = Pending
      return {
        label: 'Pending',
        icon: <Clock className="w-4 h-4" />,
        className: 'text-yellow-500',
      };
    }

    // No workflow and not downloaded
    return {
      label: 'Available',
      icon: null,
      className: 'text-gray-500',
    };
  };

  if (loading && models.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        <span className="ml-2 text-gray-500">Loading models...</span>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">AI Models</h1>
        <p className="text-gray-500 mt-2">
          Download pre-optimized TensorRT-LLM models to the shared model storage
        </p>
      </div>

      {/* Error Alert */}
      {error && <TkErrorAlert title="Error" message={error} />}

      {/* Active Downloads Summary */}
      {downloads.filter(d => d.is_running).length > 0 && (
        <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
            <span className="font-medium text-blue-700 dark:text-blue-300">
              {downloads.filter(d => d.is_running).length} download(s) in progress
            </span>
          </div>
        </div>
      )}

      {/* Models Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-900">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Model
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Server Type
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Size
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Quantization
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Status
              </th>
              <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                Action
              </th>
            </tr>
          </thead>
          <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
            {models.map((model) => {
              const status = getModelStatus(model);
              const isDownloading = isModelDownloading(model.id);

              return (
                <tr key={model.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-6 py-4">
                    <div className="flex flex-col">
                      <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                        {model.name}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400 font-mono">
                        {model.id}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-wrap gap-1">
                      {model.server_type.map((type) => (
                        <span
                          key={type}
                          className="px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-indigo-800 dark:text-indigo-300"
                        >
                          {type}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {model.size}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span className="px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full bg-purple-100 dark:bg-purple-900/30 text-purple-800 dark:text-purple-300">
                      {model.quantization}
                    </span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className={`flex items-center gap-2 ${status.className}`}>
                      {status.icon}
                      <span className="text-sm font-medium">{status.label}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm">
                    <button
                      onClick={() => handleDownload(model.id)}
                      disabled={isDownloading || status.label === 'Downloaded'}
                      className={`inline-flex items-center gap-2 px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white
                        ${
                          isDownloading || status.label === 'Downloaded'
                            ? 'bg-gray-400 cursor-not-allowed'
                            : 'bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500'
                        }`}
                    >
                      {isDownloading ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Downloading...
                        </>
                      ) : status.label === 'Downloaded' ? (
                        <>
                          <CheckCircle2 className="w-4 h-4" />
                          Downloaded
                        </>
                      ) : (
                        <>
                          <Download className="w-4 h-4" />
                          Download
                        </>
                      )}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Empty State */}
      {!loading && models.length === 0 && (
        <div className="text-center py-12">
          <p className="text-gray-500">No models available</p>
        </div>
      )}
    </div>
  );
}
