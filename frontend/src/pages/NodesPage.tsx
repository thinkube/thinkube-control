import { useState, useEffect, useCallback } from 'react';
import { TkCard, TkCardContent, TkCardHeader, TkCardTitle, TkStatCard } from 'thinkube-style/components/cards-data';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkSuccessAlert, TkErrorAlert } from 'thinkube-style/components/feedback';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
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
  Loader2,
  CheckCircle2,
  XCircle,
  Trash2,
  Monitor,
} from 'lucide-react';
import { AddNodeWizard } from '@/components/AddNodeWizard';
import { useNodesStore } from '@/stores/useNodesStore';

export default function NodesPage() {
  const {
    nodes,
    architectures,
    loading,
    error,
    listNodes,
    removeNode,
  } = useNodesStore();

  const [wizardOpen, setWizardOpen] = useState(false);
  const [addSuccess, setAddSuccess] = useState(false);
  const [removeConfirm, setRemoveConfirm] = useState<string | null>(null);

  useEffect(() => {
    listNodes();
  }, [listNodes]);

  const handleWizardComplete = useCallback(() => {
    setAddSuccess(true);
    listNodes();
    setTimeout(() => setAddSuccess(false), 10000);
  }, [listNodes]);

  const handleRemoveNode = async (hostname: string) => {
    const success = await removeNode(hostname);
    if (success) {
      setRemoveConfirm(null);
    }
  };

  const controlPlaneNodes = nodes.filter(n => n.role === 'control_plane');
  const workerNodes = nodes.filter(n => n.role === 'worker');

  return (
    <TkPageWrapper>
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

      {/* Success alert */}
      {addSuccess && (
        <TkSuccessAlert title="Node(s) Added">
          The new node(s) have been successfully joined to the cluster.
        </TkSuccessAlert>
      )}

      {error && (
        <TkErrorAlert title="Error">{error}</TkErrorAlert>
      )}

      {/* Node List */}
      <TkCard>
        <TkCardHeader className="flex flex-row items-center justify-between">
          <TkCardTitle>Cluster Nodes</TkCardTitle>
          <TkButton
            onClick={() => setWizardOpen(true)}
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
                        <TkBadge appearance="outlined" className="font-mono">
                          {node.architecture}
                        </TkBadge>
                      </TkTableCell>
                      <TkTableCell>
                        <TkBadge appearance={node.role === 'control_plane' ? 'prominent' : 'muted'}>
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
                            intent="ghost"
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

      {/* Add Node Wizard */}
      <AddNodeWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        onComplete={handleWizardComplete}
      />

      {/* Remove Node Confirmation */}
      <TkDialogRoot open={!!removeConfirm} onOpenChange={(open) => !open && setRemoveConfirm(null)}>
        <TkDialogContent>
          <TkDialogHeader>
            <TkDialogTitle>Remove Node</TkDialogTitle>
          </TkDialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to remove <strong>{removeConfirm}</strong> from the cluster?
            This will remove the node from the cluster and inventory.
          </p>
          <TkDialogFooter>
            <TkButton intent="ghost" onClick={() => setRemoveConfirm(null)}>Cancel</TkButton>
            <TkButton
              intent="danger"
              onClick={() => removeConfirm && handleRemoveNode(removeConfirm)}
              className="gap-2"
            >
              <Trash2 className="w-4 h-4" />
              Remove Node
            </TkButton>
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>
    </TkPageWrapper>
  );
}
