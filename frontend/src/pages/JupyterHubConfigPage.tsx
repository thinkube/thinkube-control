import { useState, useEffect, useMemo } from 'react'
import { Loader2, AlertCircle, CheckCircle2, Info, AlertTriangle } from 'lucide-react'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TkCard, TkCardHeader, TkCardContent, TkCardTitle, TkCardDescription } from 'thinkube-style/components/cards-data'
import { TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges'
import { TkCheckbox } from 'thinkube-style/components/forms-inputs'
import { TkSelect, TkSelectTrigger, TkSelectContent, TkSelectItem, TkSelectValue } from 'thinkube-style/components/forms-inputs'
import { TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkErrorAlert, TkSuccessAlert, TkWarningAlert, TkInfoAlert } from 'thinkube-style/components/feedback'
import api from '../lib/axios'

interface JupyterImage {
  name: string
  display_name: string
  description: string
}

interface ClusterNode {
  name: string
  capacity: {
    cpu: number
    memory: number
    gpu: number
  }
}

interface JupyterHubConfig {
  hidden_images: string[]
  default_image: string
  default_node: string | null
  default_cpu_cores: number | null
  default_memory_gb: number | null
  default_gpu_count: number | null
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

  const [availableImages, setAvailableImages] = useState<JupyterImage[]>([])
  const [clusterNodes, setClusterNodes] = useState<ClusterNode[]>([])
  const [config, setConfig] = useState<JupyterHubConfig>({
    hidden_images: [],
    default_image: '',
    default_node: null,
    default_cpu_cores: null,
    default_memory_gb: null,
    default_gpu_count: null
  })

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
    return config.default_image &&
           config.default_node &&
           !config.hidden_images.includes(config.default_image)
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

      // Load available images
      const imagesResponse = await api.get<JupyterImage[]>('/images/jupyter')
      setAvailableImages(imagesResponse.data)

      // Load current configuration
      const configResponse = await api.get<JupyterHubConfig>('/jupyterhub/config')
      setConfig(configResponse.data)
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

  // Toggle image visibility (checked = hidden)
  function toggleImageVisibility(imageName: string) {
    setConfig(prev => {
      const isCurrentlyHidden = prev.hidden_images.includes(imageName)
      const newHiddenImages = isCurrentlyHidden
        ? prev.hidden_images.filter(name => name !== imageName)
        : [...prev.hidden_images, imageName]

      // If hiding the default image, clear it
      let newDefaultImage = prev.default_image
      if (!isCurrentlyHidden && prev.default_image === imageName) {
        const visibleImages = availableImages.filter(
          img => !newHiddenImages.includes(img.name)
        )
        newDefaultImage = visibleImages.length > 0 ? visibleImages[0].name : ''
      }

      return {
        ...prev,
        hidden_images: newHiddenImages,
        default_image: newDefaultImage
      }
    })
  }

  // Set default image
  function setDefaultImage(imageName: string) {
    if (!config.hidden_images.includes(imageName)) {
      setConfig(prev => ({ ...prev, default_image: imageName }))
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

  // Load config on mount
  useEffect(() => {
    loadConfig()
  }, [])

  return (
    <TkPageWrapper
      title="JupyterHub Configuration"
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

      {/* Configuration Form */}
      {!loading && (
        <div className="space-y-6"> {/* @allowed-inline */}
          {/* Image Selection Section */}
          <TkCard>
            <TkCardHeader>
              <TkCardTitle>Image Selection</TkCardTitle>
              <TkCardDescription>
                Choose which Jupyter images are available to users and set the default
              </TkCardDescription>
            </TkCardHeader>
            <TkCardContent>
              {availableImages.length === 0 ? (
                <TkWarningAlert>
                  <AlertTriangle className="h-6 w-6" />
                  <span>No Jupyter images found</span>
                </TkWarningAlert>
              ) : (
                <div className="space-y-3"> {/* @allowed-inline */}
                  {availableImages.map((image) => {
                    const isHidden = config.hidden_images.includes(image.name)
                    const isDefault = config.default_image === image.name

                    return (
                      <TkCard key={image.name} variant={isDefault ? 'default' : 'secondary'}>
                        <TkCardContent standalone>
                          <div className="flex items-center gap-4"> {/* @allowed-inline */}
                            <TkCheckbox
                              id={`hide-${image.name}`}
                              checked={isHidden}
                              onCheckedChange={() => toggleImageVisibility(image.name)}
                            />
                            <label
                              htmlFor={`hide-${image.name}`}
                              className="flex-1 cursor-pointer"
                            >
                              <div className="font-medium">{image.display_name}</div>
                              <div className="text-sm text-muted-foreground">{image.description}</div>
                              <div className="text-xs text-muted-foreground font-mono">{image.name}</div>
                            </label>
                            <TkButton
                              onClick={() => setDefaultImage(image.name)}
                              disabled={isHidden}
                              variant={isDefault ? 'default' : 'ghost'}
                              size="sm"
                            >
                              {isDefault ? '⭐ Default' : 'Set Default'}
                            </TkButton>
                          </div>
                        </TkCardContent>
                      </TkCard>
                    )
                  })}
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
    </TkPageWrapper>
  )
}
