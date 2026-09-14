import { useState, useEffect } from 'react'
import { Loader2, AlertCircle, CheckCircle2 } from 'lucide-react'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TkCard, TkCardHeader, TkCardContent, TkCardTitle, TkCardDescription } from 'thinkube-style/components/cards-data'
import { TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges'
import { TkSelect, TkSelectTrigger, TkSelectContent, TkSelectItem, TkSelectValue } from 'thinkube-style/components/forms-inputs'
import { TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkErrorAlert, TkSuccessAlert } from 'thinkube-style/components/feedback'
import api from '../lib/axios'

type Resource = 'cpu_cores' | 'memory_gb' | 'gpus'

interface ResourceValues {
  cpu_cores: number
  memory_gb: number
  gpus: number
}

interface NodeDefaults extends ResourceValues {
  node: string
  capacity: ResourceValues
  choices: Record<Resource, number[]>
}

interface JupyterHubConfig {
  nodes: NodeDefaults[]
}

const RESOURCES: { key: Resource; label: string; unit: (value: number) => string }[] = [
  { key: 'cpu_cores', label: 'CPU cores', unit: (v) => `${v} core${v === 1 ? '' : 's'}` },
  { key: 'memory_gb', label: 'Memory', unit: (v) => `${v} GB` },
  { key: 'gpus', label: 'GPUs', unit: (v) => (v === 0 ? 'No GPU' : `${v} GPU${v === 1 ? '' : 's'}`) },
]

export default function JupyterHubConfigPage() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveSuccess, setSaveSuccess] = useState(false)
  const [nodes, setNodes] = useState<NodeDefaults[]>([])

  async function loadConfig() {
    setLoading(true)
    setError(null)
    setSaveSuccess(false)
    try {
      const response = await api.get<JupyterHubConfig>('/jupyterhub/config')
      setNodes(response.data.nodes)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load configuration')
      console.error('Error loading config:', err)
    } finally {
      setLoading(false)
    }
  }

  async function saveConfig() {
    setSaving(true)
    setError(null)
    setSaveSuccess(false)
    try {
      const response = await api.put<JupyterHubConfig>('/jupyterhub/config', {
        nodes: nodes.map(({ node, cpu_cores, memory_gb, gpus }) => ({ node, cpu_cores, memory_gb, gpus })),
      })
      setNodes(response.data.nodes)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 3000)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save configuration')
      console.error('Error saving JupyterHub config:', err)
    } finally {
      setSaving(false)
    }
  }

  function setValue(nodeName: string, key: Resource, value: number) {
    setNodes((prev) => prev.map((n) => (n.node === nodeName ? { ...n, [key]: value } : n)))
  }

  useEffect(() => {
    loadConfig()
  }, [])

  return (
    <TkPageWrapper description="Default resources for the notebook server on each node">
      {loading && (
        <div className="flex justify-center items-center py-12"> {/* @allowed-inline */}
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      )}

      {error && (
        <TkErrorAlert className="mb-6">
          <AlertCircle className="h-6 w-6" />
          <span>{error}</span>
        </TkErrorAlert>
      )}

      {saveSuccess && (
        <TkSuccessAlert className="mb-6">
          <CheckCircle2 className="h-6 w-6" />
          <span>Configuration saved successfully</span>
        </TkSuccessAlert>
      )}

      {!loading && (
        <div className="space-y-6"> {/* @allowed-inline */}
          {nodes.map((node) => (
            <TkCard key={node.node}>
              <TkCardHeader>
                <TkCardTitle>{node.node}</TkCardTitle>
                <TkCardDescription>
                  The notebook server on {node.node} starts with these resources. The node has {node.capacity.cpu_cores} cores,{' '}
                  {node.capacity.memory_gb} GB and {node.capacity.gpus} GPU{node.capacity.gpus === 1 ? '' : 's'}.
                </TkCardDescription>
              </TkCardHeader>
              <TkCardContent>
                <div className="grid gap-4 md:grid-cols-3"> {/* @allowed-inline */}
                  {RESOURCES.map(({ key, label, unit }) => (
                    <div key={key}>
                      <TkLabel htmlFor={`${node.node}-${key}`}>{label}</TkLabel>
                      <TkSelect value={node[key].toString()} onValueChange={(value) => setValue(node.node, key, parseInt(value))}>
                        <TkSelectTrigger id={`${node.node}-${key}`}>
                          <TkSelectValue placeholder={`Select ${label.toLowerCase()}`} />
                        </TkSelectTrigger>
                        <TkSelectContent>
                          {node.choices[key].map((choice) => (
                            <TkSelectItem key={choice} value={choice.toString()}>
                              {unit(choice)}
                            </TkSelectItem>
                          ))}
                        </TkSelectContent>
                      </TkSelect>
                    </div>
                  ))}
                </div>
              </TkCardContent>
            </TkCard>
          ))}

          <div className="flex gap-4 justify-end"> {/* @allowed-inline */}
            <TkButton onClick={loadConfig} intent="ghost" disabled={saving}>
              Reset
            </TkButton>
            <TkLoadingButton onClick={saveConfig} disabled={saving || nodes.length === 0} loading={saving}>
              Save Configuration
            </TkLoadingButton>
          </div>
        </div>
      )}
    </TkPageWrapper>
  )
}
