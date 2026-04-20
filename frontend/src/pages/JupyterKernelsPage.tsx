import { useState, useEffect, useRef } from 'react'
import { Loader2, AlertCircle, CheckCircle2, Info, Plus, Trash2, Play, Package, Edit2 } from 'lucide-react'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TkCard, TkCardHeader, TkCardContent, TkCardTitle, TkCardDescription } from 'thinkube-style/components/cards-data'
import { TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges'
import { TkSelect, TkSelectTrigger, TkSelectContent, TkSelectItem, TkSelectValue } from 'thinkube-style/components/forms-inputs'
import { TkLabel, TkInput } from 'thinkube-style/components/forms-inputs'
import { TkErrorAlert, TkSuccessAlert, TkInfoAlert } from 'thinkube-style/components/feedback'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import api from '../lib/axios'
import { EditPackagesModal } from '../components/EditPackagesModal'
import { PlaybookExecutor, PlaybookExecutorHandle } from '../components/PlaybookExecutor'

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
  architectures_built?: string[]
  started_at?: string
  completed_at?: string
}

export default function JupyterKernelsPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const [venvTemplates, setVenvTemplates] = useState<VenvTemplate[]>([])
  const [venvs, setVenvs] = useState<JupyterVenv[]>([])
  const [newVenvName, setNewVenvName] = useState('')
  const [newVenvTemplate, setNewVenvTemplate] = useState('')
  const [creatingVenv, setCreatingVenv] = useState(false)

  const [editPackagesOpen, setEditPackagesOpen] = useState(false)
  const [editingVenv, setEditingVenv] = useState<JupyterVenv | null>(null)

  const [buildTitle, setBuildTitle] = useState('Building Kernel')
  const playbookExecutorRef = useRef<PlaybookExecutorHandle>(null)

  async function loadVenvs() {
    setLoading(true)
    setError(null)

    try {
      const [templatesResponse, venvsResponse] = await Promise.all([
        api.get<{ templates: VenvTemplate[] }>('/jupyter-venvs/templates'),
        api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs'),
      ])
      setVenvTemplates(templatesResponse.data.templates)
      setVenvs(venvsResponse.data.venvs)
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to load kernels')
      console.error('Error loading kernels:', err)
    } finally {
      setLoading(false)
    }
  }

  async function createVenv() {
    if (!newVenvName || !newVenvTemplate) return

    setCreatingVenv(true)
    setError(null)

    try {
      await api.post('/jupyter-venvs', {
        name: newVenvName,
        parent_template: newVenvTemplate
      })

      const venvsResponse = await api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs')
      setVenvs(venvsResponse.data.venvs)
      setNewVenvName('')
      setNewVenvTemplate('')
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create kernel')
    } finally {
      setCreatingVenv(false)
    }
  }

  async function deleteVenv(venvId: string) {
    if (!confirm('Are you sure you want to delete this kernel?')) return

    try {
      await api.delete(`/jupyter-venvs/${venvId}`)
      const venvsResponse = await api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs')
      setVenvs(venvsResponse.data.venvs)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to delete kernel')
    }
  }

  function buildVenv(venvId: string) {
    const venv = venvs.find(v => v.id === venvId)
    if (!venv) return

    setVenvs(prev => prev.map(v => v.id === venvId ? { ...v, status: 'building' } : v))

    setBuildTitle(`Building Kernel: ${venv.name}`)
    const wsPath = `/api/v1/ws/jupyter-venvs/build/${venvId}`
    playbookExecutorRef.current?.startExecution(wsPath)
  }

  function handleBuildComplete(result: { status: string; message?: string }) {
    loadVenvs()
  }

  function openEditPackages(venv: JupyterVenv) {
    setEditingVenv(venv)
    setEditPackagesOpen(true)
  }

  async function savePackages(venvId: string, packages: string[]) {
    await api.put(`/jupyter-venvs/${venvId}/packages`, { packages })
    const venvsResponse = await api.get<{ venvs: JupyterVenv[], total: number }>('/jupyter-venvs')
    setVenvs(venvsResponse.data.venvs)
  }

  function getStatusBadge(status: string) {
    switch (status) {
      case 'success':
        return <TkBadge status="healthy">{status}</TkBadge>
      case 'building':
        return <TkBadge status="warning">{status}</TkBadge>
      case 'failed':
        return <TkBadge status="unhealthy">{status}</TkBadge>
      default:
        return <TkBadge status="pending">{status}</TkBadge>
    }
  }

  useEffect(() => {
    loadVenvs()
  }, [])

  return (
    <TkPageWrapper
      title="Jupyter Kernels"
      description="Create and manage Python virtual environments that appear as kernels in JupyterLab"
    >
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

      {success && (
        <TkSuccessAlert className="mb-6">
          <CheckCircle2 className="h-6 w-6" />
          <span>{success}</span>
        </TkSuccessAlert>
      )}

      {!loading && (
        <div className="space-y-6"> {/* @allowed-inline */}

          {/* Create New Kernel */}
          <TkCard>
            <TkCardHeader>
              <TkCardTitle>Create New Kernel</TkCardTitle>
              <TkCardDescription>
                Each kernel is based on a template and built natively for every architecture in your cluster.
              </TkCardDescription>
            </TkCardHeader>
            <TkCardContent>
              <div className="flex gap-4 mb-6"> {/* @allowed-inline */}
                <div className="flex-1">
                  <TkLabel htmlFor="venv-name">Kernel Name</TkLabel>
                  <TkInput
                    id="venv-name"
                    placeholder="my-custom-kernel"
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
                    Create
                  </TkLoadingButton>
                </div>
              </div>

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
            </TkCardContent>
          </TkCard>

          {/* Kernel List */}
          <TkCard>
            <TkCardHeader>
              <TkCardTitle>Kernels</TkCardTitle>
              <TkCardDescription>
                Manage your custom Jupyter kernels. Build deploys to all cluster architectures via K8s Jobs.
              </TkCardDescription>
            </TkCardHeader>
            <TkCardContent>
              {venvs.length === 0 ? (
                <TkInfoAlert>
                  <Package className="h-5 w-5" />
                  <span>No custom kernels yet. Create one above to get started.</span>
                </TkInfoAlert>
              ) : (
                <div className="space-y-3"> {/* @allowed-inline */}
                  {venvs.map((venv) => (
                    <TkCard key={venv.id} className="bg-secondary/10">
                      <TkCardContent standalone>
                        <div className="flex items-center justify-between"> {/* @allowed-inline */}
                          <div>
                            <div className="font-medium flex items-center gap-2">
                              {venv.name}
                              {venv.architectures_built && venv.architectures_built.length > 0 ? (
                                venv.architectures_built.map(arch => (
                                  <TkBadge key={arch} appearance="outlined" className="text-xs">{arch}</TkBadge>
                                ))
                              ) : venv.architecture ? (
                                <span className="text-xs text-muted-foreground">({venv.architecture})</span>
                              ) : null}
                            </div>
                            <div className="text-sm text-muted-foreground">
                              {venv.packages.length} packages | Created by {venv.created_by}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            {getStatusBadge(venv.status)}
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => openEditPackages(venv)}
                              title="Edit packages"
                              disabled={venv.status === 'building'}
                            >
                              <Edit2 className="h-4 w-4" />
                            </TkButton>
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => buildVenv(venv.id)}
                              title="Build kernel"
                              disabled={venv.status === 'building'}
                            >
                              <Play className="h-4 w-4" />
                            </TkButton>
                            <TkButton
                              intent="ghost"
                              size="sm"
                              onClick={() => deleteVenv(venv.id)}
                              title="Delete kernel"
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
        </div>
      )}

      {/* Playbook Executor for streaming build output */}
      <PlaybookExecutor
        ref={playbookExecutorRef}
        title={buildTitle}
        successMessage="Kernel built successfully for all architectures!"
        onComplete={handleBuildComplete}
      />

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
