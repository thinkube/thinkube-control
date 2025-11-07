import { useState, useEffect } from 'react'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays'
import { TkInput } from 'thinkube-style/components/forms-inputs'
import { TkTextarea } from 'thinkube-style/components/forms-inputs'
import { TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import {
  TkTkTable,
  TkTkTableBody,
  TkTkTableCell,
  TkTkTableHead,
  TkTkTableHeader,
  TkTkTableRow,
} from 'thinkube-style/components/tables'
import { Loader2, Plus, Download, Pencil, Trash2, Eye, EyeOff } from 'lucide-react'
import axios from '../lib/axios'

interface Secret {
  id: string
  name: string
  description?: string
  created_at: string
  used_by_apps: string[]
}

interface SecretForm {
  name: string
  description: string
  value: string
}

export default function SecretsPage() {
  const [secrets, setSecrets] = useState<Secret[]>([])
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [dialog, setDialog] = useState(false)
  const [deleteDialog, setDeleteDialog] = useState(false)
  const [editingSecret, setEditingSecret] = useState<Secret | null>(null)
  const [secretToDelete, setSecretToDelete] = useState<Secret | null>(null)
  const [showValue, setShowValue] = useState(false)
  const [secretForm, setSecretForm] = useState<SecretForm>({
    name: '',
    description: '',
    value: ''
  })

  const fetchSecrets = async () => {
    setLoading(true)
    try {
      const response = await axios.get<Secret[]>('/secrets/')
      setSecrets(response.data)
    } catch (error) {
      console.error('Failed to fetch secrets:', error)
    } finally {
      setLoading(false)
    }
  }

  const openCreateDialog = () => {
    setEditingSecret(null)
    setSecretForm({
      name: '',
      description: '',
      value: ''
    })
    setShowValue(false)
    setDialog(true)
  }

  const openEditDialog = (secret: Secret) => {
    setEditingSecret(secret)
    setSecretForm({
      name: secret.name,
      description: secret.description || '',
      value: ''
    })
    setShowValue(false)
    setDialog(true)
  }

  const closeDialog = () => {
    setDialog(false)
    setEditingSecret(null)
    setSecretForm({
      name: '',
      description: '',
      value: ''
    })
    setShowValue(false)
  }

  const saveSecret = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      if (editingSecret) {
        // Update existing secret
        const updateData: Partial<SecretForm> = {
          description: secretForm.description
        }
        if (secretForm.value) {
          updateData.value = secretForm.value
        }
        await axios.put(`/secrets/${editingSecret.id}`, updateData)
      } else {
        // Create new secret
        await axios.post('/secrets/', secretForm)
      }
      closeDialog()
      fetchSecrets()
    } catch (error: any) {
      console.error('Failed to save secret:', error)
      alert(error.response?.data?.detail || 'Failed to save secret')
    }
  }

  const confirmDelete = (secret: Secret) => {
    setSecretToDelete(secret)
    setDeleteDialog(true)
  }

  const deleteSecret = async () => {
    if (!secretToDelete) return
    try {
      await axios.delete(`/secrets/${secretToDelete.id}`)
      setDeleteDialog(false)
      setSecretToDelete(null)
      fetchSecrets()
    } catch (error: any) {
      console.error('Failed to delete secret:', error)
      alert(error.response?.data?.detail || 'Failed to delete secret')
    }
  }

  const formatDate = (dateString: string) => {
    if (!dateString) return ''
    return new Date(dateString).toLocaleString()
  }

  const exportToNotebooks = async () => {
    setExporting(true)
    try {
      const response = await axios.post('/secrets/export-to-notebooks')
      alert(`✅ ${response.data.message}\n\nSecrets available at: ${response.data.path}`)
    } catch (error: any) {
      console.error('Failed to export secrets:', error)
      alert(error.response?.data?.detail || 'Failed to export secrets to notebooks')
    } finally {
      setExporting(false)
    }
  }

  useEffect(() => {
    fetchSecrets()
  }, [])

  return (
    <div className="min-h-screen bg-background p-8"> {/* @allowed-inline */}
      <div className="prose prose-lg mb-6 dark:prose-invert"> {/* @allowed-inline */}
        <h1>API Secrets Management</h1>
        <p>Manage API keys and secrets that can be used by deployed applications. Secrets are encrypted and stored securely.</p>
      </div>

      {/* Action Buttons */}
      <div className="flex justify-end gap-2 mb-4"> {/* @allowed-inline */}
        <TkButton
          variant="secondary"
          onClick={exportToNotebooks}
          disabled={exporting || secrets.length === 0}
        >
          {exporting ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <Download className="h-5 w-5" />
          )}
          Export to Notebooks
        </TkButton>
        <TkButton variant="default" onClick={openCreateDialog}>
          <Plus className="h-5 w-5" />
          Add Secret
        </TkButton>
      </div>

      {/* Secrets TkTable */}
      <TkCard>
        <TkCardContent>
          {loading && (
            <div className="flex justify-center py-8"> {/* @allowed-inline */}
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          )}

          {!loading && secrets.length === 0 && (
            <div className="text-center py-8"> {/* @allowed-inline */}
              <p className="text-muted-foreground">
                No secrets found. Create your first secret to get started.
              </p>
            </div>
          )}

          {!loading && secrets.length > 0 && (
            <div className="overflow-x-auto"> {/* @allowed-inline */}
              <TkTable>
                <TkTableHeader>
                  <TkTableRow>
                    <TkTableHead>Name</TkTableHead>
                    <TkTableHead>Description</TkTableHead>
                    <TkTableHead>Created</TkTableHead>
                    <TkTableHead>Used By</TkTableHead>
                    <TkTableHead>Actions</TkTableHead>
                  </TkTableRow>
                </TkTableHeader>
                <TkTableBody>
                  {secrets.map((secret) => (
                    <TkTableRow key={secret.id}>
                      <TkTableCell className="font-mono">{secret.name}</TkTableCell>
                      <TkTableCell>{secret.description || '-'}</TkTableCell>
                      <TkTableCell>{formatDate(secret.created_at)}</TkTableCell>
                      <TkTableCell>
                        {secret.used_by_apps.length > 0 ? (
                          <div className="flex flex-wrap gap-1"> {/* @allowed-inline */}
                            {secret.used_by_apps.map((app) => (
                              <TkBadge key={app} variant="default" size="sm">
                                {app}
                              </TkBadge>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">Not in use</span>
                        )}
                      </TkTableCell>
                      <TkTableCell>
                        <div className="flex gap-2"> {/* @allowed-inline */}
                          <TkButton
                            variant="ghost"
                            size="sm"
                            onClick={() => openEditDialog(secret)}
                            title="Edit"
                          >
                            <Pencil className="h-4 w-4" />
                          </TkButton>
                          <TkButton
                            variant="ghost"
                            size="sm"
                            onClick={() => confirmDelete(secret)}
                            disabled={secret.used_by_apps.length > 0}
                            title="Delete"
                          >
                            <Trash2 className="h-4 w-4" />
                          </TkButton>
                        </div>
                      </TkTableCell>
                    </TkTableRow>
                  ))}
                </TkTableBody>
              </TkTable>
            </div>
          )}
        </TkCardContent>
      </TkCard>

      {/* Create/Edit Modal */}
      <TkDialogRoot open={dialog} onOpenChange={setDialog}>
        <TkDialogContent>
          <TkDialogHeader>
            <TkDialogTitle>
              {editingSecret ? 'Edit Secret' : 'Create Secret'}
            </TkDialogTitle>
          </TkDialogHeader>

          <form onSubmit={saveSecret} className="space-y-4"> {/* @allowed-inline */}
            <div className="space-y-2"> {/* @allowed-inline */}
              <TkLabel htmlFor="name">Secret Name</TkLabel>
              <TkInput
                id="name"
                value={secretForm.name}
                onChange={(e) => setSecretForm({ ...secretForm, name: e.target.value })}
                placeholder="e.g., HUGGINGFACE_TOKEN"
                disabled={!!editingSecret}
                required
                pattern="^[A-Z][A-Z0-9_]*$"
                title="Must be UPPERCASE with underscores only"
              />
              <p className="text-xs text-muted-foreground">
                Use UPPERCASE_WITH_UNDERSCORES format
              </p>
            </div>

            <div className="space-y-2"> {/* @allowed-inline */}
              <TkLabel htmlFor="description">Description (Optional)</TkLabel>
              <TkTextarea
                id="description"
                value={secretForm.description}
                onChange={(e) => setSecretForm({ ...secretForm, description: e.target.value })}
                placeholder="What is this secret used for?"
                rows={2}
              />
            </div>

            <div className="space-y-2"> {/* @allowed-inline */}
              <TkLabel htmlFor="value">
                {editingSecret ? 'New Value (leave empty to keep current)' : 'Secret Value'}
              </TkLabel>
              <div className="flex gap-2"> {/* @allowed-inline */}
                <TkInput
                  id="value"
                  type={showValue ? 'text' : 'password'}
                  value={secretForm.value}
                  onChange={(e) => setSecretForm({ ...secretForm, value: e.target.value })}
                  required={!editingSecret}
                  className="flex-1"
                />
                <TkButton
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => setShowValue(!showValue)}
                >
                  {showValue ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </TkButton>
              </div>
            </div>

            <TkDialogFooter>
              <TkButton type="button" variant="outline" onClick={closeDialog}>
                Cancel
              </TkButton>
              <TkButton type="submit" variant="default">
                {editingSecret ? 'Update' : 'Create'}
              </TkButton>
            </TkDialogFooter>
          </form>
        </TkDialogContent>
      </TkDialogRoot>

      {/* Delete Confirmation Modal */}
      <TkDialogRoot open={deleteDialog} onOpenChange={setDeleteDialog}>
        <TkDialogContent>
          <TkDialogHeader>
            <TkDialogTitle>Confirm Delete</TkDialogTitle>
          </TkDialogHeader>
          <p>
            Are you sure you want to delete the secret "{secretToDelete?.name}"?
            This action cannot be undone.
          </p>
          <TkDialogFooter>
            <TkButton variant="outline" onClick={() => setDeleteDialog(false)}>
              Cancel
            </TkButton>
            <TkButton variant="destructive" onClick={deleteSecret}>
              Delete
            </TkButton>
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>
    </div>
  )
}
