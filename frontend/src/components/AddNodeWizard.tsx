import { useState, useRef, useCallback } from 'react';
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkBadge } from 'thinkube-style/components/buttons-badges';
import { TkInput, TkLabel, TkCheckbox } from 'thinkube-style/components/forms-inputs';
import { TkAlert, TkAlertTitle, TkAlertDescription, TkErrorAlert, TkInfoAlert } from 'thinkube-style/components/feedback';
import {
  TkTable,
  TkTableBody,
  TkTableCell,
  TkTableHead,
  TkTableHeader,
  TkTableRow,
} from 'thinkube-style/components/tables';
import {
  Search,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Plus,
  Cpu,
  Key,
} from 'lucide-react';
import { PlaybookExecutor, type PlaybookExecutorHandle } from '@/components/PlaybookExecutor';
import { useNodesStore, type NetworkDiscoveredNode } from '@/stores/useNodesStore';
import { getToken } from '@/lib/tokenManager';

type WizardStep = 'scan' | 'select' | 'verify' | 'hardware' | 'adding';

interface AddNodeWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onComplete: () => void;
}

export function AddNodeWizard({ open, onOpenChange, onComplete }: AddNodeWizardProps) {
  const {
    networkNodes,
    networkScanning,
    networkMode,
    sshVerifying,
    hardwareDetecting,
    architectures,
    error,
    discoverNetwork,
    toggleNodeSelection,
    selectAllNodes,
    deselectAllNodes,
    verifySSH,
    detectHardwareBatch,
    addNodesBatch,
    clearNetworkNodes,
  } = useNodesStore();

  const [step, setStep] = useState<WizardStep>('scan');
  const [scanCidr, setScanCidr] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const playbookRef = useRef<PlaybookExecutorHandle>(null);

  const selectedNodes = networkNodes.filter((n) => n.selected);
  const allNeedPassword = selectedNodes.some(
    (n) => n.ssh_status === 'needs_password' || n.ssh_status === 'failed'
  );
  const allSSHReady = selectedNodes.length > 0 && selectedNodes.every(
    (n) => n.ssh_status === 'key_ok' || n.ssh_status === 'key_distributed'
  );
  const allHardwareDetected = selectedNodes.length > 0 && selectedNodes.every(
    (n) => n.hardware && !n.hardware.error
  );

  const handleScan = async () => {
    const cidrs = scanCidr
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean);
    await discoverNetwork(cidrs.length > 0 ? cidrs : undefined);
    setStep('select');
  };

  const handleVerifySSH = async () => {
    setStep('verify');
    await verifySSH(password || undefined);
  };

  const handleRetryWithPassword = async () => {
    if (!password) return;
    await verifySSH(password);
  };

  const handleDetectHardware = async () => {
    setStep('hardware');
    await detectHardwareBatch();
  };

  const handleAdd = async () => {
    const result = await addNodesBatch(password || undefined);
    if (!result?.job_id) return;

    setStep('adding');

    const nodesPayload = selectedNodes.map((n) => ({
      ip: n.ip,
      hostname: n.hardware?.hostname || n.hostname || '',
      lan_ip: n.ip,
      zerotier_ip: n.zerotier_ip || undefined,
      architecture: n.hardware?.architecture || '',
      gpu_detected: n.hardware?.gpu_detected || false,
      gpu_count: n.hardware?.gpu_count || 0,
      gpu_model: n.hardware?.gpu_model || '',
    }));

    const params = new URLSearchParams({
      nodes: JSON.stringify(nodesPayload),
      password: password || '',
    });

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getToken();
    const wsPath = `${protocol}//${window.location.host}/api/v1/nodes/ws/add-batch/${result.job_id}?${params.toString()}${token ? `&token=${token}` : ''}`;

    playbookRef.current?.startExecution(wsPath);
  };

  const handlePlaybookComplete = useCallback((result: any) => {
    if (result.status === 'success') {
      onComplete();
    }
    resetWizard();
  }, [onComplete]);

  const resetWizard = () => {
    setStep('scan');
    setScanCidr('');
    setPassword('');
    setShowPassword(false);
    clearNetworkNodes();
  };

  const handleClose = () => {
    resetWizard();
    onOpenChange(false);
  };

  const renderSSHStatus = (node: NetworkDiscoveredNode) => {
    switch (node.ssh_status) {
      case 'key_ok':
      case 'key_distributed':
        return (
          <div className="flex items-center gap-1 text-green-500">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span className="text-xs">{node.ssh_status === 'key_ok' ? 'Key OK' : 'Key sent'}</span>
          </div>
        );
      case 'needs_password':
        return (
          <div className="flex items-center gap-1 text-amber-500">
            <Key className="w-3.5 h-3.5" />
            <span className="text-xs">Needs password</span>
          </div>
        );
      case 'failed':
        return (
          <div className="flex items-center gap-1 text-red-500" title={node.ssh_error}>
            <XCircle className="w-3.5 h-3.5" />
            <span className="text-xs">Failed</span>
          </div>
        );
      default:
        return <span className="text-xs text-muted-foreground">Not tested</span>;
    }
  };

  const renderValidation = (node: NetworkDiscoveredNode) => {
    if (!node.validation) return null;
    if (node.validation.valid && node.validation.warnings.length === 0) {
      return (
        <div className="flex items-center gap-1 text-green-500">
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span className="text-xs">OK</span>
        </div>
      );
    }
    if (!node.validation.valid) {
      return (
        <div className="flex items-center gap-1 text-red-500" title={node.validation.errors.join(', ')}>
          <XCircle className="w-3.5 h-3.5" />
          <span className="text-xs">{node.validation.errors[0]}</span>
        </div>
      );
    }
    return (
      <div className="flex items-center gap-1 text-amber-500" title={node.validation.warnings.join(', ')}>
        <AlertTriangle className="w-3.5 h-3.5" />
        <span className="text-xs">Warnings</span>
      </div>
    );
  };

  // Determine which dialog step to show
  const showDialog = open && step !== 'adding';

  return (
    <>
      <TkDialogRoot open={showDialog} onOpenChange={(o) => !o && handleClose()}>
        <TkDialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <TkDialogHeader>
            <TkDialogTitle>
              {step === 'scan' && 'Add Nodes — Scan Network'}
              {step === 'select' && 'Add Nodes — Select Nodes'}
              {step === 'verify' && 'Add Nodes — Verify SSH'}
              {step === 'hardware' && 'Add Nodes — Hardware Detection'}
            </TkDialogTitle>
          </TkDialogHeader>

          {/* Step 1: Scan */}
          {step === 'scan' && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Scan the network to discover machines available to join the cluster.
                Performs a ping sweep and checks for SSH availability.
              </p>
              <p className="text-sm text-muted-foreground">
                Prerequisites: target machines must have Ubuntu 24.04 and the cluster user account configured.
                The app will handle SSH key distribution, ZeroTier setup, and cluster joining automatically.
              </p>

              <div className="space-y-2">
                <TkLabel>
                  Network CIDR(s) to scan
                  <span className="text-xs text-muted-foreground ml-2">
                    Leave empty to use the cluster default. Comma-separate for multiple subnets.
                  </span>
                </TkLabel>
                <TkInput
                  placeholder="e.g. 192.168.1.0/24 or 192.168.1.0/24, 10.0.0.0/24"
                  value={scanCidr}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setScanCidr(e.target.value)}
                />
              </div>

              {error && <TkErrorAlert title="Error">{error}</TkErrorAlert>}

              <TkDialogFooter>
                <TkButton intent="ghost" onClick={handleClose}>Cancel</TkButton>
                <TkButton onClick={handleScan} disabled={networkScanning} className="gap-2">
                  {networkScanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  {networkScanning ? 'Scanning...' : 'Scan Network'}
                </TkButton>
              </TkDialogFooter>
            </div>
          )}

          {/* Step 2: Select */}
          {step === 'select' && (
            <div className="space-y-4">
              {networkNodes.length === 0 ? (
                <TkInfoAlert title="No nodes found">
                  No new machines were discovered on the network.
                  Make sure the target machines are powered on and accessible.
                </TkInfoAlert>
              ) : (
                <>
                  <div className="flex items-center justify-between">
                    <p className="text-sm text-muted-foreground">
                      Found {networkNodes.length} node(s). Select the ones you want to add.
                    </p>
                    <div className="flex gap-2">
                      <TkButton intent="ghost" size="sm" onClick={selectAllNodes}>Select All</TkButton>
                      <TkButton intent="ghost" size="sm" onClick={deselectAllNodes}>Clear</TkButton>
                    </div>
                  </div>

                  <div className="overflow-x-auto rounded-lg border">
                    <TkTable>
                      <TkTableHeader>
                        <TkTableRow>
                          <TkTableHead className="w-10"></TkTableHead>
                          <TkTableHead className="font-semibold">IP Address</TkTableHead>
                          <TkTableHead className="font-semibold">Hostname</TkTableHead>
                          {networkMode === 'overlay' && (
                            <TkTableHead className="font-semibold">ZeroTier IP</TkTableHead>
                          )}
                          <TkTableHead className="font-semibold">SSH</TkTableHead>
                          <TkTableHead className="font-semibold">Confidence</TkTableHead>
                        </TkTableRow>
                      </TkTableHeader>
                      <TkTableBody>
                        {networkNodes.map((node) => (
                          <TkTableRow
                            key={node.ip}
                            className="cursor-pointer"
                            onClick={() => toggleNodeSelection(node.ip)}
                          >
                            <TkTableCell>
                              <TkCheckbox
                                checked={node.selected}
                                onCheckedChange={() => toggleNodeSelection(node.ip)}
                              />
                            </TkTableCell>
                            <TkTableCell className="font-mono text-sm">{node.ip}</TkTableCell>
                            <TkTableCell>{node.hostname || '—'}</TkTableCell>
                            {networkMode === 'overlay' && (
                              <TkTableCell className="font-mono text-sm">
                                {node.zerotier_ip || '—'}
                              </TkTableCell>
                            )}
                            <TkTableCell>
                              {node.ssh_available ? (
                                <div className="flex items-center gap-1 text-green-500">
                                  <CheckCircle2 className="w-3.5 h-3.5" />
                                  <span className="text-xs">Open</span>
                                </div>
                              ) : (
                                <div className="flex items-center gap-1 text-red-500">
                                  <XCircle className="w-3.5 h-3.5" />
                                  <span className="text-xs">Closed</span>
                                </div>
                              )}
                            </TkTableCell>
                            <TkTableCell>
                              <TkBadge appearance={node.confidence === 'confirmed' ? 'prominent' : 'muted'}>
                                {node.confidence}
                              </TkBadge>
                            </TkTableCell>
                          </TkTableRow>
                        ))}
                      </TkTableBody>
                    </TkTable>
                  </div>
                </>
              )}

              {error && <TkErrorAlert title="Error">{error}</TkErrorAlert>}

              <TkDialogFooter>
                <TkButton intent="ghost" onClick={() => setStep('scan')}>Back</TkButton>
                <TkButton
                  onClick={handleVerifySSH}
                  disabled={selectedNodes.length === 0 || sshVerifying}
                  className="gap-2"
                >
                  {sshVerifying ? <Loader2 className="w-4 h-4 animate-spin" /> : <Key className="w-4 h-4" />}
                  {sshVerifying ? 'Verifying...' : 'Verify SSH'}
                </TkButton>
              </TkDialogFooter>
            </div>
          )}

          {/* Step 3: Verify SSH */}
          {step === 'verify' && (
            <div className="space-y-4">
              <div className="overflow-x-auto rounded-lg border">
                <TkTable>
                  <TkTableHeader>
                    <TkTableRow>
                      <TkTableHead className="font-semibold">IP Address</TkTableHead>
                      <TkTableHead className="font-semibold">Hostname</TkTableHead>
                      <TkTableHead className="font-semibold">SSH Status</TkTableHead>
                    </TkTableRow>
                  </TkTableHeader>
                  <TkTableBody>
                    {selectedNodes.map((node) => (
                      <TkTableRow key={node.ip}>
                        <TkTableCell className="font-mono text-sm">{node.ip}</TkTableCell>
                        <TkTableCell>{node.hostname || '—'}</TkTableCell>
                        <TkTableCell>{renderSSHStatus(node)}</TkTableCell>
                      </TkTableRow>
                    ))}
                  </TkTableBody>
                </TkTable>
              </div>

              {allNeedPassword && (
                <TkAlert className="bg-warning/10 text-warning border-warning/20">
                  <Key className="h-5 w-5" />
                  <div>
                    <TkAlertTitle className="font-medium">SSH Password Required</TkAlertTitle>
                    <TkAlertDescription className="text-sm">
                      Some nodes need the cluster SSH key distributed.
                      Enter the password for the cluster user account on these machines.
                    </TkAlertDescription>
                  </div>
                </TkAlert>
              )}

              {allNeedPassword && (
                <div className="flex gap-2 items-end">
                  <div className="flex-1 space-y-1">
                    <TkLabel>SSH Password</TkLabel>
                    <TkInput
                      type={showPassword ? 'text' : 'password'}
                      placeholder="Password for the cluster user"
                      value={password}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => setPassword(e.target.value)}
                    />
                  </div>
                  <TkButton
                    intent="secondary"
                    onClick={handleRetryWithPassword}
                    disabled={!password || sshVerifying}
                    className="gap-2"
                  >
                    {sshVerifying ? <Loader2 className="w-4 h-4 animate-spin" /> : <Key className="w-4 h-4" />}
                    Distribute Key
                  </TkButton>
                </div>
              )}

              {error && <TkErrorAlert title="Error">{error}</TkErrorAlert>}

              <TkDialogFooter>
                <TkButton intent="ghost" onClick={() => setStep('select')}>Back</TkButton>
                <TkButton
                  onClick={handleDetectHardware}
                  disabled={!allSSHReady || hardwareDetecting}
                  className="gap-2"
                >
                  {hardwareDetecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Cpu className="w-4 h-4" />}
                  {hardwareDetecting ? 'Detecting...' : 'Detect Hardware'}
                </TkButton>
              </TkDialogFooter>
            </div>
          )}

          {/* Step 4: Hardware Detection Results */}
          {step === 'hardware' && (
            <div className="space-y-4">
              {hardwareDetecting ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin" />
                  <span className="ml-2">Detecting hardware on selected nodes...</span>
                </div>
              ) : (
                <>
                  <div className="overflow-x-auto rounded-lg border">
                    <TkTable>
                      <TkTableHeader>
                        <TkTableRow>
                          <TkTableHead className="font-semibold">Hostname</TkTableHead>
                          <TkTableHead className="font-semibold">Arch</TkTableHead>
                          <TkTableHead className="font-semibold">CPU</TkTableHead>
                          <TkTableHead className="font-semibold">RAM</TkTableHead>
                          <TkTableHead className="font-semibold">Disk</TkTableHead>
                          <TkTableHead className="font-semibold">GPU</TkTableHead>
                          <TkTableHead className="font-semibold">OS</TkTableHead>
                          <TkTableHead className="font-semibold">Status</TkTableHead>
                        </TkTableRow>
                      </TkTableHeader>
                      <TkTableBody>
                        {selectedNodes.map((node) => (
                          <TkTableRow key={node.ip}>
                            <TkTableCell className="font-medium">
                              {node.hardware?.hostname || node.hostname || node.ip}
                            </TkTableCell>
                            <TkTableCell>
                              {node.hardware?.normalized_arch ? (
                                <TkBadge appearance="outlined" className="font-mono">
                                  {node.hardware.normalized_arch}
                                </TkBadge>
                              ) : '—'}
                            </TkTableCell>
                            <TkTableCell>{node.hardware?.cpu_cores || '—'} cores</TkTableCell>
                            <TkTableCell>{node.hardware?.memory_gb || '—'} GB</TkTableCell>
                            <TkTableCell>{node.hardware?.disk_gb || '—'} GB</TkTableCell>
                            <TkTableCell>
                              {node.hardware?.gpu_detected
                                ? `${node.hardware.gpu_count}x ${node.hardware.gpu_model}`
                                : '—'}
                            </TkTableCell>
                            <TkTableCell className="text-xs">
                              {node.hardware?.os_release || '—'}
                            </TkTableCell>
                            <TkTableCell>{renderValidation(node)}</TkTableCell>
                          </TkTableRow>
                        ))}
                      </TkTableBody>
                    </TkTable>
                  </div>

                  {/* New architecture warning */}
                  {selectedNodes.some(
                    (n) =>
                      n.hardware?.normalized_arch &&
                      !architectures.includes(n.hardware.normalized_arch)
                  ) && (
                    <TkAlert className="bg-info/10 border-info/50">
                      <Cpu className="h-5 w-5 text-info" />
                      <div>
                        <TkAlertTitle className="font-medium">New Architecture Detected</TkAlertTitle>
                        <TkAlertDescription className="text-sm">
                          One or more nodes introduce a new architecture to the cluster.
                          After joining, container images will be rebuilt for multi-arch support.
                        </TkAlertDescription>
                      </div>
                    </TkAlert>
                  )}

                  {/* Validation errors */}
                  {selectedNodes.some((n) => n.validation && !n.validation.valid) && (
                    <TkAlert className="bg-destructive/10 text-destructive border-destructive/20">
                      <AlertTriangle className="h-5 w-5" />
                      <div>
                        <TkAlertTitle className="font-medium">Some nodes fail requirements</TkAlertTitle>
                        <TkAlertDescription className="text-sm">
                          {selectedNodes
                            .filter((n) => n.validation && !n.validation.valid)
                            .map((n) => (
                              <div key={n.ip}>
                                <strong>{n.hardware?.hostname || n.ip}:</strong>{' '}
                                {n.validation?.errors.join(', ')}
                              </div>
                            ))}
                        </TkAlertDescription>
                      </div>
                    </TkAlert>
                  )}
                </>
              )}

              {error && <TkErrorAlert title="Error">{error}</TkErrorAlert>}

              <TkDialogFooter>
                <TkButton intent="ghost" onClick={() => setStep('verify')}>Back</TkButton>
                <TkButton
                  onClick={handleAdd}
                  disabled={!allHardwareDetected}
                  className="gap-2"
                >
                  <Plus className="w-4 h-4" />
                  Add {selectedNodes.length} Node(s) to Cluster
                </TkButton>
              </TkDialogFooter>
            </div>
          )}
        </TkDialogContent>
      </TkDialogRoot>

      {/* Playbook executor for the adding step */}
      <PlaybookExecutor
        ref={playbookRef}
        title={`Adding ${selectedNodes.length} node(s) to cluster`}
        successMessage="Nodes successfully joined the cluster!"
        onComplete={handlePlaybookComplete}
      />
    </>
  );
}
