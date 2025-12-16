import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { Loader2, AlertCircle, CheckCircle2, Info, Plus, Trash2, Play, Package, Edit2, Clock } from 'lucide-react'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TkCard, TkCardHeader, TkCardContent, TkCardTitle, TkCardDescription } from 'thinkube-style/components/cards-data'
import { TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges'
import { TkSelect, TkSelectTrigger, TkSelectContent, TkSelectItem, TkSelectValue } from 'thinkube-style/components/forms-inputs'
import { TkLabel, TkInput } from 'thinkube-style/components/forms-inputs'
import { TkErrorAlert, TkSuccessAlert, TkInfoAlert, TkWarningAlert } from 'thinkube-style/components/feedback'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import api from '../lib/axios'
import { EditPackagesModal } from '../components/EditPackagesModal'

interface ClusterNode {
  name: string
  capacity: {
    cpu: number
    memory: number
    gpu: number
  }
}

interface JupyterHubConfig {
  default_node: string | null
  default_cpu_cores: number | null
  default_memory_gb: number | null
  default_gpu_count: number | null
}

interface VenvTemplate {
  id: string
  name: string
  description: string
  package_count: number
}

interface JupyterVenv {
  id: string
  name: string
  packages: string[]
  status: string
  is_template: boolean
  created_at: string
  created_by: string
  architecture?: string
  started_at?: string
  completed_at?: string
}

interface NodeCapacity {
  cpu: number
  memory: number
  gpu: number
}

export default function JupyterHubConfigPage() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveSuccess, setSaveSuccess] = useState(false)

  const [clusterNodes, setClusterNodes] = useState<ClusterNode[]>([])
  const [config, setConfig] = useState<JupyterHubConfig>({
    default_node: null,
    default_cpu_cores: null,
    default_memory_gb: null,
    default_gpu_count: null
  })

  // Venv state
  const [venvTemplates, setVenvTemplates] = useState<VenvTemplate[]>([])
  const [venvs, setVenvs] = useState<JupyterVenv[]>([])
  const [newVenvName, setNewVenvName] = useState('')
  const [newVenvTemplate, setNewVenvTemplate] = useState('')
  const [creatingVenv, setCreatingVenv] = useState(false)

  // Build state - using polling instead of WebSocket
  const [buildingVenvId, setBuildingVenvId] = useState<string | null>(null)
  const [buildWarning, setBuildWarning] = useState<string | null>(null)
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null)

  // Edit packages modal state
  const [editPackagesOpen, setEditPackagesOpen] = useState(false)
  const [editingVenv, setEditingVenv] = useState<JupyterVenv | null>(null)

  // Calculate selected node capacity
  const selectedNodeCapacity = useMemo<NodeCapacity | null>(() => {
    if (!config.default_node) return null
    const node = clusterNodes.find(n => n.name === config.default_node)
    if (!node) return null

    return {
      cpu: Math.floor(node.capacity.cpu),
      memory: parseInt(node.capacity.memory.toString()),
      gpu: node.capacity.gpu
    }
  }, [config.default_node, clusterNodes])

  // Generate CPU options based on selected node
  const cpuOptions = useMemo(() => {
    if (!selectedNodeCapacity) return []
    const options = [1, 2, 4, 6, 8, 12, 16, 24, 32]
    return options.filter(cores => cores <= selectedNodeCapacity.cpu)
  }, [selectedNodeCapacity])

  // Generate memory options based on selected node
  const memoryOptions = useMemo(() => {
    if (!selectedNodeCapacity) return []
    const options = [2, 4, 8, 16, 32, 48, 64, 96, 128]
    return options.filter(gb => gb <= selectedNodeCapacity.memory)
  }, [selectedNodeCapacity])

  // Validate configuration
  const isValid = useMemo(() => {
    return config.default_node !== null
  }, [config])

  // Parse memory string to GB
  function parseMemoryToGB(memoryStr: string): number {
    if (memoryStr.endsWith('Gi')) {
      return parseInt(memoryStr)
    } else if (memoryStr.endsWith('Mi')) {
      return Math.floor(parseInt(memoryStr) / 1024)
    }
    return 0
  }

  // Load configuration
  async function loadConfig() {
    setLoading(true)
    setError(null)
    setSaveSuccess(false)

    try {
      // Load cluster resources
      const resourcesResponse = await api.get<ClusterNode[]>('/cluster/resources')
      const parsedNodes = resourcesResponse.data.map(node => ({
        ...node,
        capacity: {
          ...node.capacity,
          memory: parseMemoryToGB(node.capacity.memory.toString())
        }
      }))
      setClusterNodes(parsedNodes)

      // Load current configuration
      const configResponse = await api.get<JupyterHubConfig>('/jupyterhub/config')
      setConfig(configResponse.data)

      // Load venv templates
      const templatesResponse = await api.get<{ templates: VenvTemplate[] }>('/jupyter-venvs/templates')
      setVenvTemplates(templatesResponse.data.templates)

      // Load existing venvs
      const venvsResponse = await api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs')
      setVenvs(venvsResponse.data.venvs)

    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load configuration')
      console.error('Error loading JupyterHub config:', err)
    } finally {
      setLoading(false)
    }
  }

  // Save configuration
  async function saveConfig() {
    if (!isValid) return

    setSaving(true)
    setError(null)
    setSaveSuccess(false)

    try {
      await api.put('/jupyterhub/config', config)
      setSaveSuccess(true)
      setTimeout(() => {
        setSaveSuccess(false)
      }, 3000)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save configuration')
      console.error('Error saving JupyterHub config:', err)
    } finally {
      setSaving(false)
    }
  }

  // Handle node change and validate resources
  function handleNodeChange(nodeName: string) {
    setConfig(prev => {
      const node = clusterNodes.find(n => n.name === nodeName)
      if (!node) return { ...prev, default_node: nodeName }

      const capacity = {
        cpu: Math.floor(node.capacity.cpu),
        memory: parseInt(node.capacity.memory.toString()),
        gpu: node.capacity.gpu
      }

      // Adjust resources if they exceed new node capacity
      const newCpuCores = prev.default_cpu_cores && prev.default_cpu_cores > capacity.cpu
        ? capacity.cpu
        : prev.default_cpu_cores
      const newMemoryGb = prev.default_memory_gb && prev.default_memory_gb > capacity.memory
        ? capacity.memory
        : prev.default_memory_gb
      const newGpuCount = prev.default_gpu_count && prev.default_gpu_count > capacity.gpu
        ? capacity.gpu
        : prev.default_gpu_count

      return {
        ...prev,
        default_node: nodeName,
        default_cpu_cores: newCpuCores,
        default_memory_gb: newMemoryGb,
        default_gpu_count: newGpuCount
      }
    })
  }

  // Create new venv
  async function createVenv() {
    if (!newVenvName || !newVenvTemplate) return

    setCreatingVenv(true)
    setError(null)

    try {
      await api.post('/jupyter-venvs', {
        name: newVenvName,
        parent_template: newVenvTemplate
      })

      // Refresh venv list
      const venvsResponse = await api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs')
      setVenvs(venvsResponse.data.venvs)

      // Clear form
      setNewVenvName('')
      setNewVenvTemplate('')

    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create venv')
    } finally {
      setCreatingVenv(false)
    }
  }

  // Delete venv
  async function deleteVenv(venvId: string) {
    if (!confirm('Are you sure you want to delete this venv?')) return

    try {
      await api.delete(`/jupyter-venvs/${venvId}`)

      // Refresh venv list
      const venvsResponse = await api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs')
      setVenvs(venvsResponse.data.venvs)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to delete venv')
    }
  }

  // Polling function to check build status
  const pollBuildStatus = useCallback(async (venvId: string) => {
    try {
      const response = await api.get<JupyterVenv>(`/jupyter-venvs/${venvId}`)
      const venv = response.data

      // Update venv in list
      setVenvs(prev => prev.map(v => v.id === venvId ? venv : v))

      // Check if build is complete
      if (venv.status !== 'building') {
        // Stop polling
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current)
          pollIntervalRef.current = null
        }
        setBuildingVenvId(null)
        setBuildWarning(null)

        if (venv.status === 'success') {
          setSaveSuccess(true)
          setTimeout(() => setSaveSuccess(false), 5000)
        } else if (venv.status === 'failed') {
          setError(`Build failed for ${venv.name}. Check logs for details.`)
        }
      }
    } catch (err: any) {
      console.error('Error polling build status:', err)
    }
  }, [])

  // Start polling when a build is in progress
  useEffect(() => {
    if (buildingVenvId) {
      // Poll immediately
      pollBuildStatus(buildingVenvId)

      // Then poll every 10 seconds
      pollIntervalRef.current = setInterval(() => {
        pollBuildStatus(buildingVenvId)
      }, 10000)

      return () => {
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current)
          pollIntervalRef.current = null
        }
      }
    }
  }, [buildingVenvId, pollBuildStatus])

  // Build venv - starts build and begins polling
  async function buildVenv(venvId: string) {
    const venv = venvs.find(v => v.id === venvId)
    if (!venv) return

    // Show confirmation with warning
    const confirmed = confirm(
      `Build venv "${venv.name}"?\n\n` +
      `⚠️ WARNING: This may take 5-15 minutes.\n\n` +
      `The build process runs on a GPU node and installs all packages. ` +
      `The page may appear idle during this time - this is normal.\n\n` +
      `Click OK to start the build.`
    )
    if (!confirmed) return

    try {
      const response = await api.post(`/jupyter-venvs/${venvId}/build`)

      // Update venv status immediately
      setVenvs(prev => prev.map(v => v.id === venvId ? { ...v, status: 'building' } : v))

      // Set warning message from response
      if (response.data.warning) {
        setBuildWarning(response.data.warning)
      }

      // Start polling
      setBuildingVenvId(venvId)

    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to start build')
    }
  }

  // Open edit packages modal
  function openEditPackages(venv: JupyterVenv) {
    setEditingVenv(venv)
    setEditPackagesOpen(true)
  }

  // Save packages
  async function savePackages(venvId: string, packages: string[]) {
    await api.put(`/jupyter-venvs/${venvId}/packages`, { packages })

    // Refresh venv list to get updated package count
    const venvsResponse = await api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs')
    setVenvs(venvsResponse.data.venvs)
  }

  // Get status badge variant
  function getStatusBadge(status: string) {
    switch (status) {
      case 'success':
        return <TkBadge variant="success">{status}</TkBadge>
      case 'building':
        return <TkBadge variant="warning">{status}</TkBadge>
      case 'failed':
        return <TkBadge variant="destructive">{status}</TkBadge>
      default:
        return <TkBadge variant="secondary">{status}</TkBadge>
    }
  }

  // Load config on mount
  useEffect(() => {
    loadConfig()
  }, [])

  return (
    <TkPageWrapper
      title="JupyterHub Config"
      description="Configure default JupyterHub settings for users"
    >
      {/* Loading State */}
      {loading && (
        <div className="flex justify-center items-center py-12"> {/* @allowed-inline */}
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      )}

      {/* Error Alert */}
      {error && (
        <TkErrorAlert className="mb-6">
          <AlertCircle className="h-6 w-6" />
          <span>{error}</span>
        </TkErrorAlert>
      )}

      {/* Success Alert */}
      {saveSuccess && (
        <TkSuccessAlert className="mb-6">
          <CheckCircle2 className="h-6 w-6" />
          <span>Configuration saved successfully</span>
        </TkSuccessAlert>
      )}

      {/* Build Warning Alert */}
      {buildWarning && (
        <TkWarningAlert className="mb-6">
          <Clock className="h-6 w-6" />
          <div>
            <strong>Build in progress</strong>
            <p className="text-sm mt-1">{buildWarning}</p>
          </div>
        </TkWarningAlert>
      )}

      {/* Configuration Form */}
      {!loading && (
        <div className="space-y-6"> {/* @allowed-inline */}

          {/* Jupyter Kernels / Venvs Section */}
          <TkCard>
            <TkCardHeader>
              <TkCardTitle>Jupyter Kernels (Venvs)</TkCardTitle>
              <TkCardDescription>
                Create and manage Python virtual environments that appear as kernels in JupyterLab.
                Each venv is based on a template (fine-tuning or agent-dev).
              </TkCardDescription>
            </TkCardHeader>
            <TkCardContent>
              {/* Create new venv form */}
              <div className="flex gap-4 mb-6"> {/* @allowed-inline */}
                <div className="flex-1">
                  <TkLabel htmlFor="venv-name">Venv Name</TkLabel>
                  <TkInput
                    id="venv-name"
                    placeholder="my-custom-venv"
                    value={newVenvName}
                    onChange={(e) => setNewVenvName(e.target.value)}
                  />
                </div>
                <div className="flex-1">
                  <TkLabel htmlFor="venv-template">Base Template</TkLabel>
                  <TkSelect
                    value={newVenvTemplate}
                    onValueChange={setNewVenvTemplate}
                  >
                    <TkSelectTrigger id="venv-template">
                      <TkSelectValue placeholder="Select template" />
                    </TkSelectTrigger>
                    <TkSelectContent>
                      {venvTemplates.map((template) => (
                        <TkSelectItem key={template.id} value={template.id}>
                          {template.name} ({template.package_count} packages)
                        </TkSelectItem>
                      ))}
                    </TkSelectContent>
                  </TkSelect>
                </div>
                <div className="flex items-end">
                  <TkLoadingButton
                    onClick={createVenv}
                    disabled={!newVenvName || !newVenvTemplate || creatingVenv}
                    loading={creatingVenv}
                  >
                    <Plus className="h-4 w-4 mr-2" />
                    Create Venv
                  </TkLoadingButton>
                </div>
              </div>

              {/* Template info */}
              <TkInfoAlert className="mb-4">
                <Info className="h-5 w-5" />
                <div>
                  <strong>Templates:</strong>
                  <ul className="list-disc ml-6 mt-1">
                    <li><strong>fine-tuning</strong>: Base ML + bitsandbytes, peft, trl, Unsloth for model fine-tuning</li>
                    <li><strong>agent-dev</strong>: Base ML + LangChain, CrewAI, AG2, LangGraph for AI agents</li>
                  </ul>
                </div>
              </TkInfoAlert>

              {/* Existing venvs */}
              {venvs.length === 0 ? (
                <TkInfoAlert>
                  <Package className="h-5 w-5" />
                  <span>No custom venvs created yet. Create one above or use the built-in templates.</span>
                </TkInfoAlert>
              ) : (
                <div className="space-y-3"> {/* @allowed-inline */}
                  {venvs.map((venv) => (
                    <TkCard key={venv.id} variant="secondary">
                      <TkCardContent standalone>
                        <div className="flex items-center justify-between"> {/* @allowed-inline */}
                          <div>
                            <div className="font-medium flex items-center gap-2">
                              {venv.name}
                              {venv.architecture && (
                                <span className="text-xs text-muted-foreground">({venv.architecture})</span>
                              )}
                            </div>
                            <div className="text-sm text-muted-foreground">
                              {venv.packages.length} packages | Created by {venv.created_by}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            {venv.status === 'building' && buildingVenvId === venv.id ? (
                              <div className="flex items-center gap-2">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                <TkBadge variant="warning">building...</TkBadge>
                              </div>
                            ) : (
                              getStatusBadge(venv.status)
                            )}
                            <TkButton
                              variant="ghost"
                              size="sm"
                              onClick={() => openEditPackages(venv)}
                              title="Edit packages"
                              disabled={venv.status === 'building'}
                            >
                              <Edit2 className="h-4 w-4" />
                            </TkButton>
                            <TkButton
                              variant="ghost"
                              size="sm"
                              onClick={() => buildVenv(venv.id)}
                              title="Build venv"
                              disabled={venv.status === 'building'}
                            >
                              <Play className="h-4 w-4" />
                            </TkButton>
                            <TkButton
                              variant="ghost"
                              size="sm"
                              onClick={() => deleteVenv(venv.id)}
                              title="Delete venv"
                              className="text-destructive"
                              disabled={venv.status === 'building'}
                            >
                              <Trash2 className="h-4 w-4" />
                            </TkButton>
                          </div>
                        </div>
                      </TkCardContent>
                    </TkCard>
                  ))}
                </div>
              )}
            </TkCardContent>
          </TkCard>

          {/* Default Resources Section */}
          <TkCard>
            <TkCardHeader>
              <TkCardTitle>Default Resources</TkCardTitle>
              <TkCardDescription>
                Set default CPU, memory, and GPU allocations for new JupyterHub instances
              </TkCardDescription>
            </TkCardHeader>
            <TkCardContent>
              {/* Node Selection */}
              <div className="mb-4"> {/* @allowed-inline */}
                <TkLabel htmlFor="node-select">Default Node</TkLabel>
                <TkSelect
                  value={config.default_node || ''}
                  onValueChange={handleNodeChange}
                >
                  <TkSelectTrigger id="node-select">
                    <TkSelectValue placeholder="Select a node" />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {clusterNodes.map((node) => (
                      <TkSelectItem key={node.name} value={node.name}>
                        {node.name} ({node.capacity.cpu} cores, {node.capacity.memory} GB, {node.capacity.gpu} GPUs)
                      </TkSelectItem>
                    ))}
                  </TkSelectContent>
                </TkSelect>
              </div>

              {/* Resource Selectors */}
              {selectedNodeCapacity ? (
                <div className="space-y-6"> {/* @allowed-inline */}
                  {/* CPU */}
                  <div>
                    <TkLabel htmlFor="cpu-select">Default CPU Cores</TkLabel>
                    <TkSelect
                      value={config.default_cpu_cores?.toString() || ''}
                      onValueChange={(value) => setConfig(prev => ({ ...prev, default_cpu_cores: parseInt(value) }))}
                    >
                      <TkSelectTrigger id="cpu-select">
                        <TkSelectValue placeholder="Select CPU cores" />
                      </TkSelectTrigger>
                      <TkSelectContent>
                        {cpuOptions.map((cores) => (
                          <TkSelectItem key={cores} value={cores.toString()}>
                            {cores} core{cores > 1 ? 's' : ''}
                          </TkSelectItem>
                        ))}
                      </TkSelectContent>
                    </TkSelect>
                  </div>

                  {/* Memory */}
                  <div>
                    <TkLabel htmlFor="memory-select">Default Memory</TkLabel>
                    <TkSelect
                      value={config.default_memory_gb?.toString() || ''}
                      onValueChange={(value) => setConfig(prev => ({ ...prev, default_memory_gb: parseInt(value) }))}
                    >
                      <TkSelectTrigger id="memory-select">
                        <TkSelectValue placeholder="Select memory" />
                      </TkSelectTrigger>
                      <TkSelectContent>
                        {memoryOptions.map((gb) => (
                          <TkSelectItem key={gb} value={gb.toString()}>
                            {gb} GB
                          </TkSelectItem>
                        ))}
                      </TkSelectContent>
                    </TkSelect>
                  </div>

                  {/* GPU */}
                  <div>
                    <TkLabel htmlFor="gpu-select">Default GPU Count</TkLabel>
                    <TkSelect
                      value={config.default_gpu_count?.toString() || ''}
                      onValueChange={(value) => setConfig(prev => ({ ...prev, default_gpu_count: parseInt(value) }))}
                    >
                      <TkSelectTrigger id="gpu-select">
                        <TkSelectValue placeholder="Select GPU count" />
                      </TkSelectTrigger>
                      <TkSelectContent>
                        {Array.from({ length: selectedNodeCapacity.gpu + 1 }, (_, i) => i).map((count) => (
                          <TkSelectItem key={count} value={count.toString()}>
                            {count} GPU{count !== 1 ? 's' : ''}
                          </TkSelectItem>
                        ))}
                      </TkSelectContent>
                    </TkSelect>
                  </div>
                </div>
              ) : (
                <TkInfoAlert>
                  <Info className="h-6 w-6" />
                  <span>Please select a node first to configure resources</span>
                </TkInfoAlert>
              )}
            </TkCardContent>
          </TkCard>

          {/* Action Buttons */}
          <div className="flex gap-4 justify-end"> {/* @allowed-inline */}
            <TkButton
              onClick={loadConfig}
              variant="ghost"
              disabled={saving}
            >
              Reset
            </TkButton>
            <TkLoadingButton
              onClick={saveConfig}
              disabled={saving || !isValid}
              loading={saving}
            >
              Save Configuration
            </TkLoadingButton>
          </div>
        </div>
      )}

      {/* Edit Packages Modal */}
      <EditPackagesModal
        open={editPackagesOpen}
        onOpenChange={setEditPackagesOpen}
        venv={editingVenv}
        onSave={savePackages}
        onBuild={buildVenv}
      />
    </TkPageWrapper>
  )
}
