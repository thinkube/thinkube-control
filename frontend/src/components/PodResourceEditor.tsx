import { useState } from 'react';
import { useServicesStore } from '@/stores/useServicesStore';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkInput } from 'thinkube-style/components/forms-inputs';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';

interface PodResourceEditorProps {
  serviceId: string;
  podName: string;
  containerName: string;
  currentResources?: {
    cpu_request?: string;
    memory_request?: string;
    cpu_limit?: string;
    memory_limit?: string;
  };
  onClose: () => void;
  onResized?: () => void;
}

export function PodResourceEditor({
  serviceId,
  podName,
  containerName,
  currentResources,
  onClose,
  onResized,
}: PodResourceEditorProps) {
  const { resizePodResources } = useServicesStore();
  const [cpuRequest, setCpuRequest] = useState(currentResources?.cpu_request || '');
  const [cpuLimit, setCpuLimit] = useState(currentResources?.cpu_limit || '');
  const [memoryRequest, setMemoryRequest] = useState(currentResources?.memory_request || '');
  const [memoryLimit, setMemoryLimit] = useState(currentResources?.memory_limit || '');
  const [loading, setLoading] = useState(false);

  const handleResize = async () => {
    const resources: Record<string, string> = {};
    if (cpuRequest && cpuRequest !== currentResources?.cpu_request) resources.cpu_request = cpuRequest;
    if (cpuLimit && cpuLimit !== currentResources?.cpu_limit) resources.cpu_limit = cpuLimit;
    if (memoryRequest && memoryRequest !== currentResources?.memory_request) resources.memory_request = memoryRequest;
    if (memoryLimit && memoryLimit !== currentResources?.memory_limit) resources.memory_limit = memoryLimit;

    if (Object.keys(resources).length === 0) {
      toast.info('No changes to apply');
      return;
    }

    setLoading(true);
    try {
      const result = await resizePodResources(serviceId, podName, containerName, resources);
      toast.success(`Resources resized (${result.resize_status})`);
      onResized?.();
      onClose();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to resize resources');
    } finally {
      setLoading(false);
    }
  };

  return (
    <TkCard className="border-2">
      <TkCardHeader>
        <TkCardTitle className="text-sm">
          Resize: {containerName}
        </TkCardTitle>
      </TkCardHeader>
      <TkCardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-xs text-muted-foreground">CPU Request</label>
            <TkInput
              value={cpuRequest}
              onChange={(e) => setCpuRequest(e.target.value)}
              placeholder="e.g. 250m"
              className="text-xs"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">CPU Limit</label>
            <TkInput
              value={cpuLimit}
              onChange={(e) => setCpuLimit(e.target.value)}
              placeholder="e.g. 1000m"
              className="text-xs"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Memory Request</label>
            <TkInput
              value={memoryRequest}
              onChange={(e) => setMemoryRequest(e.target.value)}
              placeholder="e.g. 256Mi"
              className="text-xs"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Memory Limit</label>
            <TkInput
              value={memoryLimit}
              onChange={(e) => setMemoryLimit(e.target.value)}
              placeholder="e.g. 1Gi"
              className="text-xs"
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          CPU changes apply in-place. Memory changes may restart the container.
        </p>
        <div className="flex gap-2 justify-end">
          <TkButton size="sm" intent="secondary" onClick={onClose} disabled={loading}>
            Cancel
          </TkButton>
          <TkButton size="sm" onClick={handleResize} disabled={loading}>
            {loading ? <><Loader2 className="h-3 w-3 animate-spin mr-1" /> Resizing...</> : 'Apply'}
          </TkButton>
        </div>
      </TkCardContent>
    </TkCard>
  );
}
