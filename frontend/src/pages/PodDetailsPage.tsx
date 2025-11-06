import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useServicesStore } from '@/stores/useServicesStore';
import { ArrowLeft, Loader2, Download, RefreshCw, Copy } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkSeparator, TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkTabsRoot, TkTabsList, TkTabsTrigger, TkTabsContent } from 'thinkube-style/components/navigation';
import { TkInput, TkSelect, TkSelectTrigger, TkSelectValue, TkSelectContent, TkSelectItem } from 'thinkube-style/components/forms-inputs';
import { toast } from 'sonner';

interface PodDescription {
  formatted?: string;
}

interface ContainerLog {
  logs: string;
}

export default function PodDetailsPage() {
  const { id, podName } = useParams<{ id: string; podName: string }>();
  const navigate = useNavigate();
  const { describePod, getContainerLogs, fetchServiceDetails } = useServicesStore();

  const [loading, setLoading] = useState(true);
  const [podDescription, setPodDescription] = useState<string>('');
  const [containers, setContainers] = useState<string[]>([]);
  const [activeContainer, setActiveContainer] = useState<string>('');
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [loadingLogs, setLoadingLogs] = useState<Record<string, boolean>>({});
  const [logLines, setLogLines] = useState(100);
  const [logSearch, setLogSearch] = useState('');
  const logsContainerRef = useRef<HTMLPreElement>(null);

  // Fetch pod description on mount
  useEffect(() => {
    if (!id || !podName) return;

    const loadPodDescription = async () => {
      setLoading(true);
      try {
        // Fetch service details to get pod info with containers
        const serviceDetails = await fetchServiceDetails(id);
        const pod = serviceDetails.pods?.find((p: any) => p.name === podName);

        if (pod && pod.containers) {
          const containerNames = pod.containers.map((c: any) => c.name);
          setContainers(containerNames);
          if (containerNames.length > 0) {
            setActiveContainer(containerNames[0]);
          }
        }

        // Still fetch pod description for the Description tab
        const response = await describePod(id, podName);
        const formatted = response.formatted || JSON.stringify(response, null, 2);
        setPodDescription(formatted);
      } catch (error) {
        toast.error('Failed to load pod details');
        setPodDescription('Error: Failed to get pod description');
      } finally {
        setLoading(false);
      }
    };

    loadPodDescription();
  }, [id, podName, describePod, fetchServiceDetails]);

  // Load logs for active container
  useEffect(() => {
    if (!id || !podName || !activeContainer || logs[activeContainer]) return;

    const loadLogs = async () => {
      setLoadingLogs(prev => ({ ...prev, [activeContainer]: true }));
      try {
        const response = await getContainerLogs(id, podName, activeContainer, logLines);
        const logData = typeof response === 'string' ? response : (response as ContainerLog).logs;
        setLogs(prev => ({ ...prev, [activeContainer]: logData }));

        // Auto-scroll to bottom
        setTimeout(() => {
          if (logsContainerRef.current) {
            logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
          }
        }, 100);
      } catch (error) {
        toast.error(`Failed to load logs for ${activeContainer}`);
        setLogs(prev => ({ ...prev, [activeContainer]: 'Error: Failed to get container logs' }));
      } finally {
        setLoadingLogs(prev => ({ ...prev, [activeContainer]: false }));
      }
    };

    loadLogs();
  }, [id, podName, activeContainer, logLines, getContainerLogs, logs]);

  const handleRefreshLogs = async () => {
    if (!id || !podName || !activeContainer) return;

    setLoadingLogs(prev => ({ ...prev, [activeContainer]: true }));
    try {
      const response = await getContainerLogs(id, podName, activeContainer, logLines);
      const logData = typeof response === 'string' ? response : (response as ContainerLog).logs;
      setLogs(prev => ({ ...prev, [activeContainer]: logData }));
      toast.success('Logs refreshed');

      // Auto-scroll to bottom
      setTimeout(() => {
        if (logsContainerRef.current) {
          logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
        }
      }, 100);
    } catch (error) {
      toast.error('Failed to refresh logs');
    } finally {
      setLoadingLogs(prev => ({ ...prev, [activeContainer]: false }));
    }
  };

  const handleDownloadLogs = () => {
    if (!activeContainer || !logs[activeContainer]) return;

    const blob = new Blob([logs[activeContainer]], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${podName}-${activeContainer}-logs.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success('Logs downloaded');
  };

  const handleCopyDescription = () => {
    navigator.clipboard.writeText(podDescription).then(() => {
      toast.success('Pod description copied to clipboard');
    }).catch(() => {
      toast.error('Failed to copy to clipboard');
    });
  };

  const handleLogLinesChange = (value: string) => {
    const newLines = Number(value);
    setLogLines(newLines);
    // Clear current logs to force reload
    if (activeContainer) {
      setLogs(prev => {
        const newLogs = { ...prev };
        delete newLogs[activeContainer];
        return newLogs;
      });
    }
  };

  const filteredLogs = () => {
    if (!activeContainer || !logs[activeContainer]) return '';
    if (!logSearch) return logs[activeContainer];

    const lines = logs[activeContainer].split('\n');
    return lines.filter(line =>
      line.toLowerCase().includes(logSearch.toLowerCase())
    ).join('\n');
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Loader2 className="animate-spin h-8 w-8 mx-auto mb-4" />
          <p className="text-muted-foreground">Loading pod details...</p>
        </div>
      </div>
    );
  }

  return (
    <TkPageWrapper>
      {/* Back button */}
      <div className="mb-6">
        <TkButton
          variant="ghost"
          size="sm"
          onClick={() => navigate(`/services/${id}`)}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Service Details
        </TkButton>
      </div>

      <div className="space-y-6">

      {/* Tabs for Description and Logs */}
      <TkTabsRoot defaultValue="logs">
        <TkTabsList>
          <TkTabsTrigger value="description">Description</TkTabsTrigger>
          <TkTabsTrigger value="logs">Logs</TkTabsTrigger>
        </TkTabsList>

        {/* Pod Description Tab */}
        <TkTabsContent value="description">
          <TkCard>
            <TkCardHeader>
              <div className="flex items-center justify-between">
                <TkCardTitle>Pod Description</TkCardTitle>
                <TkButton
                  size="sm"
                  variant="outline"
                  onClick={handleCopyDescription}
                >
                  <Copy className="h-4 w-4 mr-2" />
                  Copy
                </TkButton>
              </div>
            </TkCardHeader>
            <TkCardContent>
              <pre className="text-xs font-mono overflow-auto whitespace-pre-wrap break-words">
                {podDescription}
              </pre>
            </TkCardContent>
          </TkCard>
        </TkTabsContent>

        {/* Container Logs Tab */}
        <TkTabsContent value="logs">
          <TkCard>
            <TkCardHeader>
              <TkCardTitle>Container Logs</TkCardTitle>
            </TkCardHeader>
            <TkCardContent>
              {containers.length > 0 ? (
                <TkTabsRoot value={activeContainer} onValueChange={setActiveContainer}>
                  <TkTabsList>
                    {containers.map((container) => (
                      <TkTabsTrigger key={container} value={container}>
                        {container}
                      </TkTabsTrigger>
                    ))}
                  </TkTabsList>

                  {containers.map((container) => (
                    <TkTabsContent key={container} value={container}>
                      {/* Controls Bar */}
                      <div className="flex flex-wrap gap-2 mb-4">
                        {/* Line selector */}
                        <TkSelect value={logLines.toString()} onValueChange={handleLogLinesChange}>
                          <TkSelectTrigger className="w-40">
                            <TkSelectValue placeholder="Select lines" />
                          </TkSelectTrigger>
                          <TkSelectContent>
                            <TkSelectItem value="50">Last 50 lines</TkSelectItem>
                            <TkSelectItem value="100">Last 100 lines</TkSelectItem>
                            <TkSelectItem value="200">Last 200 lines</TkSelectItem>
                            <TkSelectItem value="500">Last 500 lines</TkSelectItem>
                            <TkSelectItem value="1000">Last 1000 lines</TkSelectItem>
                          </TkSelectContent>
                        </TkSelect>

                        {/* Search */}
                        <TkInput
                          type="text"
                          placeholder="Filter logs..."
                          value={logSearch}
                          onChange={(e) => setLogSearch(e.target.value)}
                          className="flex-1 min-w-[200px]"
                        />

                        {/* Actions */}
                        <TkButton
                          size="sm"
                          variant="outline"
                          onClick={handleRefreshLogs}
                          disabled={loadingLogs[container]}
                        >
                          <RefreshCw className={`h-4 w-4 mr-2 ${loadingLogs[container] ? 'animate-spin' : ''}`} />
                          Refresh
                        </TkButton>
                        <TkButton
                          size="sm"
                          variant="outline"
                          onClick={handleDownloadLogs}
                          disabled={!logs[container]}
                        >
                          <Download className="h-4 w-4 mr-2" />
                          Download
                        </TkButton>
                      </div>

                      {/* Logs Display */}
                      {loadingLogs[container] ? (
                        <div className="flex items-center justify-center h-64">
                          <Loader2 className="animate-spin h-6 w-6" />
                        </div>
                      ) : (
                        <div>
                          {logSearch && (
                            <div className="text-sm text-muted-foreground mb-2">
                              Filtered results shown
                            </div>
                          )}
                          <pre
                            ref={logsContainerRef}
                            className="text-xs font-mono overflow-auto h-96 whitespace-pre-wrap break-words"
                            style={{ maxHeight: '600px' }}
                          >
                            {filteredLogs() || 'No logs available'}
                          </pre>
                        </div>
                      )}
                    </TkTabsContent>
                  ))}
                </TkTabsRoot>
              ) : (
                <p className="text-sm text-muted-foreground">No containers found</p>
              )}
            </TkCardContent>
          </TkCard>
        </TkTabsContent>
      </TkTabsRoot>
      </div>
    </TkPageWrapper>
  );
}
