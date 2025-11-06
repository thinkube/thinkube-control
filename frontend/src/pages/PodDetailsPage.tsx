import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useServicesStore } from '@/stores/useServicesStore';
import { ArrowLeft, Loader2, Download, RefreshCw } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkSeparator } from 'thinkube-style/components/utilities';
import { TkTabsRoot, TkTabsList, TkTabsTrigger, TkTabsContent } from 'thinkube-style/components/navigation';
import { toast } from 'sonner';

export default function PodDetailsPage() {
  const { id, podName } = useParams<{ id: string; podName: string }>();
  const navigate = useNavigate();
  const { describePod, getContainerLogs } = useServicesStore();

  const [loading, setLoading] = useState(true);
  const [podDetails, setPodDetails] = useState<any>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [loadingLogs, setLoadingLogs] = useState<Record<string, boolean>>({});
  const [activeContainer, setActiveContainer] = useState<string | null>(null);
  const [logLines, setLogLines] = useState(100);

  // Fetch pod details
  useEffect(() => {
    if (!id || !podName) return;

    const loadPodDetails = async () => {
      setLoading(true);
      try {
        const details = await describePod(id, podName);
        setPodDetails(details);

        // Set active container to first container
        if (details.containers && details.containers.length > 0) {
          setActiveContainer(details.containers[0].name);
        }
      } catch (error) {
        console.error('Failed to load pod details:', error);
        toast.error('Failed to load pod details');
      } finally {
        setLoading(false);
      }
    };

    loadPodDetails();
  }, [id, podName, describePod]);

  // Load logs for active container
  useEffect(() => {
    if (!id || !podName || !activeContainer) return;

    const loadLogs = async () => {
      setLoadingLogs(prev => ({ ...prev, [activeContainer]: true }));
      try {
        const logData = await getContainerLogs(id, podName, activeContainer, logLines);
        setLogs(prev => ({ ...prev, [activeContainer]: logData.logs || logData }));
      } catch (error) {
        console.error('Failed to load logs:', error);
        toast.error(`Failed to load logs for ${activeContainer}`);
      } finally {
        setLoadingLogs(prev => ({ ...prev, [activeContainer]: false }));
      }
    };

    // Only load if not already loaded
    if (!logs[activeContainer]) {
      loadLogs();
    }
  }, [id, podName, activeContainer, logLines, getContainerLogs, logs]);

  const handleRefreshLogs = async () => {
    if (!id || !podName || !activeContainer) return;

    setLoadingLogs(prev => ({ ...prev, [activeContainer]: true }));
    try {
      const logData = await getContainerLogs(id, podName, activeContainer, logLines);
      setLogs(prev => ({ ...prev, [activeContainer]: logData.logs || logData }));
      toast.success('Logs refreshed');
    } catch (error) {
      console.error('Failed to refresh logs:', error);
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

  if (loading || !podDetails) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Loader2 className="animate-spin h-8 w-8 mx-auto mb-4" />
          <p className="text-muted-foreground">Loading pod details...</p>
        </div>
      </div>
    );
  }

  const containers = podDetails.containers || [];

  return (
    <div className="space-y-6">
      {/* Back button */}
      <div>
        <TkButton
          variant="ghost"
          size="sm"
          onClick={() => navigate(`/services/${id}`)}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Service Details
        </TkButton>
      </div>

      {/* Pod Header */}
      <TkCard>
        <TkCardHeader>
          <TkCardTitle className="text-2xl">{podName}</TkCardTitle>
        </TkCardHeader>
        <TkCardContent className="space-y-4">
          {/* Basic Info */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-muted-foreground">Status:</span>
              <TkBadge className="ml-2" variant={podDetails.status === 'Running' ? 'success' : 'destructive'}>
                {podDetails.status || 'Unknown'}
              </TkBadge>
            </div>
            {podDetails.node && (
              <div>
                <span className="text-muted-foreground">Node:</span>
                <span className="ml-2 font-medium">{podDetails.node}</span>
              </div>
            )}
            {podDetails.restarts !== undefined && (
              <div>
                <span className="text-muted-foreground">Restarts:</span>
                <span className="ml-2 font-medium">{podDetails.restarts}</span>
              </div>
            )}
            {podDetails.age && (
              <div>
                <span className="text-muted-foreground">Age:</span>
                <span className="ml-2 font-medium">{podDetails.age}</span>
              </div>
            )}
          </div>

          {/* Conditions */}
          {podDetails.conditions && podDetails.conditions.length > 0 && (
            <>
              <TkSeparator />
              <div>
                <h4 className="font-medium mb-2">Conditions</h4>
                <div className="space-y-1 text-sm">
                  {podDetails.conditions.map((condition: any, index: number) => (
                    <div key={index} className="flex justify-between">
                      <span className="text-muted-foreground">{condition.type}:</span>
                      <TkBadge variant={condition.status === 'True' ? 'success' : 'secondary'} className="text-xs">
                        {condition.status}
                      </TkBadge>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </TkCardContent>
      </TkCard>

      {/* Container Logs */}
      {containers.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <div className="flex items-center justify-between">
              <TkCardTitle>Container Logs</TkCardTitle>
              <div className="flex gap-2">
                <TkButton
                  size="sm"
                  variant="outline"
                  onClick={handleRefreshLogs}
                  disabled={!activeContainer || loadingLogs[activeContainer]}
                >
                  <RefreshCw className={`h-4 w-4 mr-2 ${loadingLogs[activeContainer] ? 'animate-spin' : ''}`} />
                  Refresh
                </TkButton>
                <TkButton
                  size="sm"
                  variant="outline"
                  onClick={handleDownloadLogs}
                  disabled={!activeContainer || !logs[activeContainer]}
                >
                  <Download className="h-4 w-4 mr-2" />
                  Download
                </TkButton>
              </div>
            </div>
          </TkCardHeader>
          <TkCardContent>
            <TkTabsRoot value={activeContainer || containers[0]?.name} onValueChange={setActiveContainer}>
              <TkTabsList>
                {containers.map((container: any) => (
                  <TkTabsTrigger key={container.name} value={container.name}>
                    {container.name}
                  </TkTabsTrigger>
                ))}
              </TkTabsList>

              {containers.map((container: any) => (
                <TkTabsContent key={container.name} value={container.name}>
                  <div className="space-y-4">
                    {/* Container Info */}
                    <div className="text-sm space-y-2">
                      {container.image && (
                        <div>
                          <span className="text-muted-foreground">Image:</span>
                          <span className="ml-2 font-mono text-xs">{container.image}</span>
                        </div>
                      )}
                      {container.state && (
                        <div>
                          <span className="text-muted-foreground">State:</span>
                          <TkBadge className="ml-2" variant={container.ready ? 'success' : 'secondary'}>
                            {container.state}
                          </TkBadge>
                        </div>
                      )}
                    </div>

                    <TkSeparator />

                    {/* Logs */}
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-sm font-medium">Logs (last {logLines} lines)</h4>
                        <select
                          className="text-sm"
                          value={logLines}
                          onChange={(e) => {
                            setLogLines(Number(e.target.value));
                            setLogs(prev => ({ ...prev, [container.name]: '' }));
                          }}
                        >
                          <option value={50}>50 lines</option>
                          <option value={100}>100 lines</option>
                          <option value={200}>200 lines</option>
                          <option value={500}>500 lines</option>
                          <option value={1000}>1000 lines</option>
                        </select>
                      </div>

                      {loadingLogs[container.name] ? (
                        <div className="flex items-center justify-center h-64">
                          <Loader2 className="animate-spin h-6 w-6" />
                        </div>
                      ) : (
                        <pre className="text-xs font-mono overflow-x-auto max-h-96 overflow-y-auto whitespace-pre-wrap break-words">
                          {logs[container.name] || 'No logs available'}
                        </pre>
                      )}
                    </div>
                  </div>
                </TkTabsContent>
              ))}
            </TkTabsRoot>
          </TkCardContent>
        </TkCard>
      )}
    </div>
  );
}
