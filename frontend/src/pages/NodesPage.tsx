import { useState, useEffect, useRef, useCallback } from 'react';
import { TkCard, TkCardContent, TkCardHeader, TkCardTitle, TkStatCard } from 'thinkube-style/components/cards-data';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkAlert, TkAlertDescription, TkAlertTitle, TkSuccessAlert, TkErrorAlert } from 'thinkube-style/components/feedback';
import { TkInput, TkLabel } from 'thinkube-style/components/forms-inputs';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkSeparator } from 'thinkube-style/components/utilities';
import {
  TkTable,
  TkTableBody,
  TkTableCell,
  TkTableHead,
  TkTableHeader,
  TkTableRow,
} from 'thinkube-style/components/tables';
import {
  Server,
  Plus,
  Cpu,
  MemoryStick,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Trash2,
  Search,
  Monitor,
} from 'lucide-react';
import { PlaybookExecutor, type PlaybookExecutorHandle } from '@/components/PlaybookExecutor';
import { useNodesStore, type ClusterNode, type DiscoveredNode } from '@/stores/useNodesStore';
import { getToken } from '@/lib/tokenManager';

type WizardStep = 'idle' | 'discover' | 'confirm' | 'adding';

export default function NodesPage() {
  const {
    nodes,
    architectures,
    loading,
    error,
    discoveredNode,
    discovering,
    listNodes,
    discoverNode,
    addNode,
    removeNode,
    clearDiscoveredNode,
  } = useNodesStore();

  const [wizardStep, setWizardStep] = useState<WizardStep>('idle');
  const [nodeIP, setNodeIP] = useState('');
  const [nodeUsername, setNodeUsername] = useState('');
  const [zerotierIP, setZerotierIP] = useState('');
  const [addJobId, setAddJobId] = useState('');
  const [addSuccess, setAddSuccess] = useState(false);
  const [newArchDetected, setNewArchDetected] = useState(false);
  const [removeConfirm, setRemoveConfirm] = useState<string | null>(null);
  const [rebuildActions, setRebuildActions] = useState<Array<{action: string; description: string; detail?: string}>>([]);

  const playbookRef = useRef<PlaybookExecutorHandle>(null);

  useEffect(() => {
    listNodes();
  }, [listNodes]);

  const handleDiscover = async () => {
    if (!nodeIP) return;
    setWizardStep('discover');
    const result = await discoverNode(nodeIP, nodeUsername || undefined);
    if (result) {
      setWizardStep('confirm');
    } else {
      setWizardStep('discover');
    }
  };

  const handleAddNode = async () => {
    if (!discoveredNode) return;

    const result = await addNode({
      hostname: discoveredNode.hostname,
      ip: discoveredNode.ip,
      architecture: discoveredNode.architecture,
      zerotier_ip: zerotierIP || undefined,
      lan_ip: discoveredNode.ip,
      gpu_detected: discoveredNode.gpu_detected,
      gpu_count: discoveredNode.gpu_count,
      gpu_model: discoveredNode.gpu_model,
    });

    if (result?.job_id) {
      setAddJobId(result.job_id);
      setWizardStep('adding');

      const params = new URLSearchParams({
        hostname: discoveredNode.hostname,
        ip: discoveredNode.ip,
        architecture: discoveredNode.architecture,
        zerotier_ip: zerotierIP || '',
        lan_ip: discoveredNode.ip,
        gpu_detected: String(discoveredNode.gpu_detected),
        gpu_count: String(discoveredNode.gpu_count),
        gpu_model: discoveredNode.gpu_model || '',
      });

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const token = getToken();
      const wsPath = `${protocol}//${window.location.host}/api/v1/nodes/ws/add/${result.job_id}?${params.toString()}${token ? `&token=${token}` : ''}`;

      playbookRef.current?.startExecution(wsPath);
    }
  };

  const handleAddComplete = useCallback((result: any) => {
    if (result.status === 'success') {
      setAddSuccess(true);
      if (result.rebuild_actions?.length > 0) {
        setRebuildActions(result.rebuild_actions);
      }
      listNodes();
    }
    setWizardStep('idle');
  }, [listNodes]);

  const handleRemoveNode = async (hostname: string) => {
    const success = await removeNode(hostname);
    if (success) {
      setRemoveConfirm(null);
    }
  };

  const resetWizard = () => {
    setWizardStep('idle');
    setNodeIP('');
    setNodeUsername('');
    setZerotierIP('');
    setAddJobId('');
    setAddSuccess(false);
    setNewArchDetected(false);
    setRebuildActions([]);
    clearDiscoveredNode();
  };

  const controlPlaneNodes = nodes.filter(n => n.role === 'control_plane');
  const workerNodes = nodes.filter(n => n.role === 'worker');

  return (
    <div className="space-y-6">
      {/* Cluster Overview */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <TkStatCard
          title="Total Nodes"
          value={nodes.length}
          description={`${controlPlaneNodes.length} control plane, ${workerNodes.length} workers`}
          icon={Server}
          variant="primary"
        />
        <TkStatCard
          title="Architectures"
          value={architectures.length}
          description={architectures.join(', ') || 'Loading...'}
          icon={Cpu}
          variant="primary"
        />
        <TkStatCard
          title="Total CPUs"
          value={nodes.reduce((sum, n) => sum + n.cpu_capacity, 0)}
          description="cores across all nodes"
          icon={Cpu}
          variant="primary"
        />
        <TkStatCard
          title="Total GPUs"
          value={nodes.reduce((sum, n) => sum + n.gpu_count, 0)}
          description="GPUs available"
          icon={Monitor}
          variant="primary"
        />
      </div>

      {/* Success/Error alerts */}
      {addSuccess && (
        <TkSuccessAlert
          title="Node Added"
          description="The new node has been successfully joined to the cluster."
        />
      )}

      {rebuildActions.length > 0 && (
        <TkAlert className="bg-warning/10 text-warning border-warning/20">
          <AlertTriangle className="h-5 w-5" />
          <div>
            <TkAlertTitle className="font-medium">New Architecture — Rebuild Required</TkAlertTitle>
            <TkAlertDescription>
              <p className="text-sm mb-2">
                A new architecture was added to the cluster. The following actions are recommended:
              </p>
              <ul className="text-sm space-y-1 list-disc list-inside">
                {rebuildActions.map((action, i) => (
                  <li key={i}>
                    <span className="font-medium">{action.description}</span>
                    {action.detail && <span className="text-muted-foreground"> — {action.detail}</span>}
                  </li>
                ))}
              </ul>
            </TkAlertDescription>
          </div>
        </TkAlert>
      )}

      {error && (
        <TkErrorAlert
          title="Error"
          description={error}
        />
      )}

      {/* Node List */}
      <TkCard>
        <TkCardHeader className="flex flex-row items-center justify-between">
          <TkCardTitle>Cluster Nodes</TkCardTitle>
          <TkButton
            onClick={() => { resetWizard(); setWizardStep('discover'); }}
            className="gap-2"
          >
            <Plus className="w-4 h-4" />
            Add Node
          </TkButton>
        </TkCardHeader>
        <TkCardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin" />
              <span className="ml-2">Loading nodes...</span>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead className="font-semibold">Node</TkTableHead>
                    <TkTableHead className="font-semibold">Architecture</TkTableHead>
                    <TkTableHead className="font-semibold">Role</TkTableHead>
                    <TkTableHead className="font-semibold">Status</TkTableHead>
                    <TkTableHead className="font-semibold">CPU</TkTableHead>
                    <TkTableHead className="font-semibold">Memory</TkTableHead>
                    <TkTableHead className="font-semibold">GPUs</TkTableHead>
                    <TkTableHead className="font-semibold">Version</TkTableHead>
                    <TkTableHead className="font-semibold">Actions</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {nodes.map((node) => (
                    <TkTableRow key={node.name}>
                      <TkTableCell className="font-medium">{node.name}</TkTableCell>
                      <TkTableCell>
                        <TkBadge variant="outline" className="font-mono">
                          {node.architecture}
                        </TkBadge>
                      </TkTableCell>
                      <TkTableCell>
                        <TkBadge variant={node.role === 'control_plane' ? 'default' : 'secondary'}>
                          {node.role === 'control_plane' ? 'Control Plane' : 'Worker'}
                        </TkBadge>
                      </TkTableCell>
                      <TkTableCell>
                        {node.ready ? (
                          <div className="flex items-center gap-1 text-green-500">
                            <CheckCircle2 className="w-4 h-4" />
                            Ready
                          </div>
                        ) : (
                          <div className="flex items-center gap-1 text-red-500">
                            <XCircle className="w-4 h-4" />
                            Not Ready
                          </div>
                        )}
                      </TkTableCell>
                      <TkTableCell>{node.cpu_capacity} cores</TkTableCell>
                      <TkTableCell>{node.memory_capacity_gb} GB</TkTableCell>
                      <TkTableCell>{node.gpu_count > 0 ? node.gpu_count : '-'}</TkTableCell>
                      <TkTableCell className="text-xs text-muted-foreground">
                        {node.kubelet_version}
                      </TkTableCell>
                      <TkTableCell>
                        {node.role !== 'control_plane' && (
                          <TkButton
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            onClick={() => setRemoveConfirm(node.name)}
                          >
                            <Trash2 className="w-4 h-4" />
                          </TkButton>
                        )}
                      </TkTableCell>
                    </TkTableRow>
                  ))}
                </TkTableBody>
              </TkTable>
            </div>
          )}
        </TkCardContent>
      </TkCard>

      {/* Add Node Dialog */}
      <TkDialogRoot open={wizardStep === 'discover' || wizardStep === 'confirm'} onOpenChange={(open) => !open && resetWizard()}>
        <TkDialogContent className="max-w-lg">
          <TkDialogHeader>
            <TkDialogTitle>
              {wizardStep === 'discover' ? 'Discover Node' : 'Confirm Node Addition'}
            </TkDialogTitle>
          </TkDialogHeader>

          {wizardStep === 'discover' && (
            <div className="space-y-4">
              <div className="space-y-2">
                <TkLabel>Node IP Address</TkLabel>
                <TkInput
                  placeholder="e.g. 172.16.0.10 or 192.168.191.x"
                  value={nodeIP}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNodeIP(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <TkLabel>
                  SSH Username
                  <span className="text-xs text-muted-foreground ml-2">Optional, defaults to system user</span>
                </TkLabel>
                <TkInput
                  placeholder="tkadmin"
                  value={nodeUsername}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNodeUsername(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <TkLabel>
                  ZeroTier IP
                  <span className="text-xs text-muted-foreground ml-2">For overlay networking</span>
                </TkLabel>
                <TkInput
                  placeholder="e.g. 192.168.191.20"
                  value={zerotierIP}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setZerotierIP(e.target.value)}
                />
              </div>

              {error && (
                <TkErrorAlert title="Discovery Failed" description={error} />
              )}

              <TkDialogFooter>
                <TkButton variant="ghost" onClick={resetWizard}>Cancel</TkButton>
                <TkButton onClick={handleDiscover} disabled={!nodeIP || discovering} className="gap-2">
                  {discovering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  {discovering ? 'Discovering...' : 'Discover'}
                </TkButton>
              </TkDialogFooter>
            </div>
          )}

          {wizardStep === 'confirm' && discoveredNode && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-muted-foreground">Hostname</span>
                  <p className="font-medium">{discoveredNode.hostname}</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Architecture</span>
                  <p className="font-medium font-mono">{discoveredNode.normalized_arch} ({discoveredNode.architecture})</p>
                </div>
                <div>
                  <span className="text-muted-foreground">CPU</span>
                  <p className="font-medium">{discoveredNode.cpu_cores} cores</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Memory</span>
                  <p className="font-medium">{discoveredNode.memory_gb} GB</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Disk</span>
                  <p className="font-medium">{discoveredNode.disk_gb} GB</p>
                </div>
                <div>
                  <span className="text-muted-foreground">OS</span>
                  <p className="font-medium">{discoveredNode.os_release}</p>
                </div>
                {discoveredNode.gpu_detected && (
                  <>
                    <div>
                      <span className="text-muted-foreground">GPU</span>
                      <p className="font-medium">{discoveredNode.gpu_count}x {discoveredNode.gpu_model}</p>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Driver</span>
                      <p className="font-medium">{discoveredNode.nvidia_driver_version || 'Not installed'}</p>
                    </div>
                  </>
                )}
              </div>

              {discoveredNode.k8s_installed && (
                <TkAlert className="bg-warning/10 text-warning border-warning/20">
                  <AlertTriangle className="h-5 w-5" />
                  <TkAlertDescription>
                    k8s snap is already installed on this node. It may have been previously joined to a cluster.
                  </TkAlertDescription>
                </TkAlert>
              )}

              {!architectures.includes(discoveredNode.normalized_arch) && architectures.length > 0 && (
                <TkAlert className="bg-info/10 border-info/50">
                  <Cpu className="h-5 w-5 text-info" />
                  <div>
                    <TkAlertTitle className="font-medium">New Architecture Detected</TkAlertTitle>
                    <TkAlertDescription className="text-sm">
                      This node introduces <strong>{discoveredNode.normalized_arch}</strong> to
                      the cluster (currently: {architectures.join(', ')}). After joining,
                      container images may need to be rebuilt for the new architecture.
                    </TkAlertDescription>
                  </div>
                </TkAlert>
              )}

              <TkDialogFooter>
                <TkButton variant="ghost" onClick={() => { setWizardStep('discover'); clearDiscoveredNode(); }}>
                  Back
                </TkButton>
                <TkButton onClick={handleAddNode} className="gap-2">
                  <Plus className="w-4 h-4" />
                  Add to Cluster
                </TkButton>
              </TkDialogFooter>
            </div>
          )}
        </TkDialogContent>
      </TkDialogRoot>

      {/* Playbook Executor for node addition */}
      <PlaybookExecutor
        ref={playbookRef}
        title={`Adding node ${discoveredNode?.hostname || ''}`}
        successMessage="Node successfully joined the cluster!"
        onComplete={handleAddComplete}
      />

      {/* Remove Node Confirmation */}
      <TkDialogRoot open={!!removeConfirm} onOpenChange={(open) => !open && setRemoveConfirm(null)}>
        <TkDialogContent>
          <TkDialogHeader>
            <TkDialogTitle>Remove Node</TkDialogTitle>
          </TkDialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to remove <strong>{removeConfirm}</strong> from the cluster?
            This will drain all workloads and remove the node from the inventory.
          </p>
          <TkDialogFooter>
            <TkButton variant="ghost" onClick={() => setRemoveConfirm(null)}>Cancel</TkButton>
            <TkButton
              variant="destructive"
              onClick={() => removeConfirm && handleRemoveNode(removeConfirm)}
              className="gap-2"
            >
              <Trash2 className="w-4 h-4" />
              Remove Node
            </TkButton>
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>
    </div>
  );
}
