import { useEffect } from 'react';
import { Download } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useModelDownloadsStore } from '../stores/useModelDownloadsStore';

export function DownloadIndicator() {
  const navigate = useNavigate();
  const { fetchDownloads, getActiveDownloadsCount, startPolling, stopPolling } = useModelDownloadsStore();

  const activeCount = getActiveDownloadsCount();

  useEffect(() => {
    // Fetch downloads on mount
    fetchDownloads();

    // Start polling
    startPolling();

    // Cleanup
    return () => {
      stopPolling();
    };
  }, []);

  // Don't show if no active downloads
  if (activeCount === 0) {
    return null;
  }

  return (
    <button
      onClick={() => navigate('/models')}
      className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-900/50 transition-colors"
      title="View active downloads"
    >
      <Download className="w-4 h-4" />
      <span className="text-sm font-medium">{activeCount}</span>
    </button>
  );
}
