/*
 * Copyright 2025 Alejandro Martinez Corria and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect } from 'react'
import { TkDialogRoot, TkDialogContent, TkDialogHeader, TkDialogTitle, TkDialogFooter, TkDialogDescription } from 'thinkube-style/components/modals-overlays'
import { TkButton, TkLoadingButton } from 'thinkube-style/components/buttons-badges'
import { TkTextarea, TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkInfoAlert } from 'thinkube-style/components/feedback'
import { Info } from 'lucide-react'

interface JupyterVenv {
  id: string
  name: string
  packages: string[]
  status: string
  is_template: boolean
  created_at: string
  created_by: string
}

interface EditPackagesModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  venv: JupyterVenv | null
  onSave: (venvId: string, packages: string[]) => Promise<void>
  onBuild?: (venvId: string) => void
}

export function EditPackagesModal({ open, onOpenChange, venv, onSave, onBuild }: EditPackagesModalProps) {
  const [packages, setPackages] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Sync packages when venv changes
  useEffect(() => {
    if (venv) {
      setPackages(venv.packages.join('\n'))
      setError(null)
    }
  }, [venv])

  async function handleSave() {
    if (!venv) return

    setSaving(true)
    setError(null)

    try {
      const packageList = packages
        .split('\n')
        .map(p => p.trim())
        .filter(p => p.length > 0)

      await onSave(venv.id, packageList)
      onOpenChange(false)
    } catch (err: any) {
      setError(err.message || 'Failed to save packages')
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveAndBuild() {
    if (!venv) return

    setSaving(true)
    setError(null)

    try {
      const packageList = packages
        .split('\n')
        .map(p => p.trim())
        .filter(p => p.length > 0)

      await onSave(venv.id, packageList)
      onOpenChange(false)

      // Trigger build after saving
      if (onBuild) {
        onBuild(venv.id)
      }
    } catch (err: any) {
      setError(err.message || 'Failed to save packages')
    } finally {
      setSaving(false)
    }
  }

  return (
    <TkDialogRoot open={open} onOpenChange={onOpenChange}>
      <TkDialogContent className="max-w-2xl">
        <TkDialogHeader>
          <TkDialogTitle>Edit Packages - {venv?.name}</TkDialogTitle>
          <TkDialogDescription>
            Customize the Python packages for this virtual environment
          </TkDialogDescription>
        </TkDialogHeader>

        <div className="space-y-4 py-4"> {/* @allowed-inline */}
          <TkInfoAlert>
            <Info className="h-5 w-5" />
            <span>
              Enter one package per line. You can specify versions (e.g., <code>numpy==1.24.0</code>).
              Base packages from the template are included automatically.
            </span>
          </TkInfoAlert>

          <div>
            <TkLabel htmlFor="packages">Additional Packages</TkLabel>
            <TkTextarea
              id="packages"
              value={packages}
              onChange={(e) => setPackages(e.target.value)}
              placeholder="numpy&#10;pandas&#10;scikit-learn>=1.0&#10;transformers"
              rows={12}
              className="font-mono text-sm"
            />
          </div>

          {error && (
            <div className="text-destructive text-sm">{error}</div>
          )}
        </div>

        <TkDialogFooter>
          <TkButton variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </TkButton>
          <TkLoadingButton variant="secondary" onClick={handleSave} loading={saving}>
            Save
          </TkLoadingButton>
          <TkLoadingButton onClick={handleSaveAndBuild} loading={saving}>
            Save & Build
          </TkLoadingButton>
        </TkDialogFooter>
      </TkDialogContent>
    </TkDialogRoot>
  )
}
