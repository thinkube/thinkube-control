/*
 * Copyright 2025 Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect } from 'react'
import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from 'thinkube-style/components/modals-overlays'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkInput } from 'thinkube-style/components/forms-inputs'
import { TkTextarea } from 'thinkube-style/components/forms-inputs'
import { TkCheckbox } from 'thinkube-style/components/forms-inputs'
import { TkRadioGroup, TkRadioGroupItem } from 'thinkube-style/components/forms-inputs'
import { TkLabel } from 'thinkube-style/components/forms-inputs'
import {
  TkSelect,
  TkSelectTrigger,
  TkSelectContent,
  TkSelectItem,
  TkSelectGroup,
  TkSelectLabel,
  TkSelectValue,
} from 'thinkube-style/components/forms-inputs'
import { TkCard, TkCardContent } from 'thinkube-style/components/cards-data'
import { Loader2 } from 'lucide-react'
import api from '@/lib/axios'

interface BaseImage {
  value: string
  label: string
  type: 'jupyter' | 'standard'
  source: string
  template: string | null
}

interface ExistingBuild {
  id: string
  name: string
  status: string
  scope: string
  is_base: boolean
  registry_url?: string
}

interface CreateCustomImageModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImageCreated: () => void
}

interface FormData {
  creation_mode: 'from_base' | 'extend_existing'
  name: string
  scope: 'standard' | 'jupyter'
  base_image: string
  custom_base_image: string
  parent_image_id: string
  description: string
  dockerfile_content: string
  is_base: boolean
}

export function CreateCustomImageModal({
  open,
  onOpenChange,
  onImageCreated,
}: CreateCustomImageModalProps) {
  const [loading, setLoading] = useState(false)
  const [baseImages, setBaseImages] = useState<Record<string, BaseImage>>({})
  const [baseImageOptions, setBaseImageOptions] = useState<BaseImage[]>([])
  const [existingBuilds, setExistingBuilds] = useState<ExistingBuild[]>([])

  const [formData, setFormData] = useState<FormData>({
    creation_mode: 'from_base',
    name: '',
    scope: 'standard',
    base_image: 'library/ubuntu:22.04',
    custom_base_image: '',
    parent_image_id: '',
    description: '',
    dockerfile_content: '',
    is_base: false,
  })

  // Computed: Jupyter base images
  const jupyterBaseImages = baseImageOptions.filter((img) => img.type === 'jupyter')

  // Computed: Standard base images
  const standardBaseImages = baseImageOptions.filter((img) => img.type === 'standard')

  // Computed: Grouped existing builds
  const groupedImages = (() => {
    const groups = {
      standard: { name: 'standard', label: 'Standard Images', images: [] as ExistingBuild[] },
      jupyter: { name: 'jupyter', label: 'Jupyter Images', images: [] as ExistingBuild[] },
    }

    existingBuilds
      .filter((img) => img.is_base || img.status === 'success')
      .forEach((img) => {
        // Map old scopes to new types
        let type: 'standard' | 'jupyter' = 'standard'
        if (img.scope === 'jupyter') {
          type = 'jupyter'
        }
        if (groups[type]) {
          groups[type].images.push(img)
        }
      })

    // Only return groups that have images
    return Object.values(groups).filter((g) => g.images.length > 0)
  })()

  // Reset form
  const resetForm = () => {
    setFormData({
      creation_mode: 'from_base',
      name: '',
      scope: 'standard',
      base_image: 'library/ubuntu:22.04',
      custom_base_image: '',
      parent_image_id: '',
      description: '',
      dockerfile_content: '',
      is_base: false,
    })
  }

  // Load base image registry
  const loadBaseImages = async () => {
    try {
      const response = await api.get('/custom-images/base-registry')
      const data = response.data

      const allImages: BaseImage[] = []

      if (data.images && Array.isArray(data.images)) {
        data.images.forEach((image: any) => {
          allImages.push({
            value: image.registry_url || `library/${image.name}`,
            label: image.display_name || image.name,
            type: image.type || 'standard',
            source: image.source || 'predefined',
            template: image.template || null,
          })
        })
      }

      setBaseImageOptions(allImages)

      // Store templates and metadata
      const templates: Record<string, BaseImage> = {}
      allImages.forEach((img) => {
        templates[img.value] = img
      })
      setBaseImages(templates)

      // Set default if current selection not in list
      if (allImages.length > 0 && !allImages.find((img) => img.value === formData.base_image)) {
        setFormData((prev) => ({ ...prev, base_image: allImages[0].value }))
      }
    } catch (error: any) {
      console.error('Failed to load base images:', error)
      if (error.response) {
        console.error('Response error:', error.response.data)
      }
    }
  }

  // Load existing builds
  const loadExistingBuilds = async () => {
    try {
      const response = await api.get('/custom-images')
      setExistingBuilds(response.data.builds || [])
    } catch (error) {
      console.error('Failed to load existing builds:', error)
    }
  }

  // Handle mode change
  const handleModeChange = (mode: 'from_base' | 'extend_existing') => {
    setFormData((prev) => ({
      ...prev,
      creation_mode: mode,
      dockerfile_content: '',
      parent_image_id: '',
    }))

    if (mode === 'from_base') {
      loadTemplate()
    }
  }

  // Load template for base image
  const loadTemplate = () => {
    const baseImage = formData.base_image

    // Skip if custom base image
    if (baseImage === 'custom') {
      setFormData((prev) => ({
        ...prev,
        dockerfile_content: '',
        scope: 'standard',
      }))
      return
    }

    const baseImageInfo = baseImages[baseImage]

    if (baseImageInfo) {
      // Use the type (scope) from the loaded metadata
      const newScope = baseImageInfo.type || 'standard'

      // Use template if available
      if (baseImageInfo.template) {
        setFormData((prev) => ({
          ...prev,
          scope: newScope,
          dockerfile_content: baseImageInfo.template || '',
        }))
      } else {
        // No template, generate simple FROM
        setFormData((prev) => ({
          ...prev,
          scope: newScope,
          dockerfile_content: `FROM ${baseImage}

# Image: ${formData.name || '<image-name>'}
# Description: ${formData.description || 'Custom Docker image'}

WORKDIR /app

# Add your customizations here
`,
        }))
      }
    } else {
      // Fallback for unknown base images
      const baseImageName = baseImage.replace('library/', '')
      const detectedScope = baseImageName.includes('jupyter') ? 'jupyter' : 'standard'

      const updateCmd = baseImageName.includes('alpine')
        ? 'RUN apk update && apk upgrade'
        : baseImageName.includes('ubuntu') || baseImageName.includes('debian')
          ? 'RUN apt-get update && apt-get upgrade -y && apt-get clean && rm -rf /var/lib/apt/lists/*'
          : '# Base image package updates handled upstream'

      const defaultCmd = baseImageName.includes('node')
        ? 'CMD ["node", "index.js"]'
        : baseImageName.includes('python')
          ? 'CMD ["python", "app.py"]'
          : 'CMD ["/bin/sh"]'

      setFormData((prev) => ({
        ...prev,
        scope: detectedScope,
        dockerfile_content: `FROM ${baseImage}

# Image: ${formData.name || '<image-name>'}
# Description: ${formData.description || 'Custom Docker image'}

# Update system packages if applicable
${updateCmd}

# Add your customizations here
# Install additional packages
# Copy application files
# Set environment variables

# Default command
${defaultCmd}
`,
      }))
    }
  }

  // Load parent dockerfile
  const loadParentDockerfile = async (parentId: string) => {
    if (!parentId) return

    try {
      const response = await api.get(`/custom-images/${parentId}/dockerfile`)
      let parentDockerfile = response.data.dockerfile

      // Get parent info to use its registry URL and inherit scope
      const parent = existingBuilds.find((b) => b.id === parentId)

      if (parent) {
        // Replace the FROM line to point to the actual built parent image
        if (parent.registry_url) {
          const fromRegex = /^FROM\s+.+$/m
          if (fromRegex.test(parentDockerfile)) {
            parentDockerfile = parentDockerfile.replace(fromRegex, `FROM ${parent.registry_url}`)
          }
        }

        // Inherit scope from parent
        setFormData((prev) => ({
          ...prev,
          scope: (parent.scope as 'standard' | 'jupyter') || 'standard',
        }))
      }

      // Extend the parent dockerfile
      setFormData((prev) => ({
        ...prev,
        dockerfile_content: `# Extending from: ${response.data.image_name}
${parentDockerfile}

# ===== Extended customizations =====
# Add your additional customizations below

`,
      }))
    } catch (error) {
      console.error('Failed to load parent dockerfile:', error)
    }
  }

  // Create image
  const createImage = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)

    try {
      let base_image = ''

      if (formData.creation_mode === 'from_base') {
        base_image =
          formData.base_image === 'custom' ? formData.custom_base_image : formData.base_image
      }

      const payload = {
        name: formData.name,
        dockerfile_content: formData.dockerfile_content,
        scope: formData.scope,
        is_base: formData.is_base,
        parent_image_id: formData.parent_image_id || null,
        build_config: {
          base_image: base_image,
          description: formData.description || '',
          creation_mode: formData.creation_mode,
        },
      }

      await api.post('/custom-images', payload)

      onImageCreated()
      onOpenChange(false)
      resetForm()
    } catch (error: any) {
      alert(`Failed to create image: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // Load data when modal opens
  useEffect(() => {
    if (open) {
      Promise.all([loadBaseImages(), loadExistingBuilds()]).then(() => {
        // Set initial template
        if (formData.creation_mode === 'from_base') {
          loadTemplate()
        }
      })
    } else {
      resetForm()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Reload template when base_image changes (for from_base mode)
  useEffect(() => {
    if (formData.creation_mode === 'from_base' && Object.keys(baseImages).length > 0) {
      loadTemplate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formData.base_image])

  return (
    <TkDialogRoot open={open} onOpenChange={onOpenChange}>
      <TkDialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <TkDialogHeader>
          <TkDialogTitle>Create Custom Docker Image</TkDialogTitle>
        </TkDialogHeader>

        <form onSubmit={createImage} className="space-y-6">
          {/* Creation Mode Selection */}
          <TkCard>
            <TkCardContent className="pt-6">
              <TkLabel className="text-base font-semibold mb-3 block">Creation Mode</TkLabel>
              <TkRadioGroup
                value={formData.creation_mode}
                onValueChange={(value) =>
                  handleModeChange(value as 'from_base' | 'extend_existing')
                }
                className="flex gap-4"
              >
                <div className="flex items-center gap-2">
                  <TkRadioGroupItem value="from_base" id="from_base" />
                  <TkLabel htmlFor="from_base" className="font-normal cursor-pointer">
                    From Base Image
                  </TkLabel>
                </div>
                <div className="flex items-center gap-2">
                  <TkRadioGroupItem value="extend_existing" id="extend_existing" />
                  <TkLabel htmlFor="extend_existing" className="font-normal cursor-pointer">
                    Extend Existing Build
                  </TkLabel>
                </div>
              </TkRadioGroup>
            </TkCardContent>
          </TkCard>

          {/* Image Name */}
          <TkCard>
            <TkCardContent className="pt-6">
              <TkLabel htmlFor="name" className="text-base font-semibold mb-3 block">
                Image Name
              </TkLabel>
              <TkInput
                id="name"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="my-custom-app"
                required
                pattern="[a-z0-9-]+"
                title="Only lowercase letters, numbers and hyphens"
              />
              <p className="text-sm text-muted-foreground mt-2">
                This will be the image name in Harbor
              </p>
            </TkCardContent>
          </TkCard>

          {/* Image Type */}
          <TkCard>
            <TkCardContent className="pt-6">
              <TkLabel htmlFor="scope" className="text-base font-semibold mb-3 block">
                Image Type
              </TkLabel>
              <TkSelect
                value={formData.scope}
                onValueChange={(value) =>
                  setFormData({ ...formData, scope: value as 'standard' | 'jupyter' })
                }
                disabled={
                  formData.creation_mode === 'from_base' && formData.base_image !== 'custom'
                }
              >
                <TkSelectTrigger id="scope">
                  <TkSelectValue />
                </TkSelectTrigger>
                <TkSelectContent>
                  <TkSelectItem value="standard">Standard</TkSelectItem>
                  <TkSelectItem value="jupyter">Jupyter Notebook</TkSelectItem>
                </TkSelectContent>
              </TkSelect>
              <p className="text-sm text-muted-foreground mt-2">
                {formData.creation_mode === 'from_base' && formData.base_image !== 'custom'
                  ? 'Auto-selected based on base image'
                  : 'Select Jupyter if this image will be used with JupyterHub'}
              </p>
            </TkCardContent>
          </TkCard>

          {/* Base Image Selection (for from_base mode) */}
          {formData.creation_mode === 'from_base' && (
            <TkCard>
              <TkCardContent className="pt-6">
                <TkLabel htmlFor="base_image" className="text-base font-semibold mb-3 block">
                  Base Image
                </TkLabel>
                <TkSelect
                  value={formData.base_image}
                  onValueChange={(value) => setFormData({ ...formData, base_image: value })}
                >
                  <TkSelectTrigger id="base_image">
                    <TkSelectValue />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {jupyterBaseImages.length > 0 && (
                      <TkSelectGroup>
                        <TkSelectLabel>Jupyter Images</TkSelectLabel>
                        {jupyterBaseImages.map((image) => (
                          <TkSelectItem key={image.value} value={image.value}>
                            {image.label}
                          </TkSelectItem>
                        ))}
                      </TkSelectGroup>
                    )}
                    {standardBaseImages.length > 0 && (
                      <TkSelectGroup>
                        <TkSelectLabel>Standard Images</TkSelectLabel>
                        {standardBaseImages.map((image) => (
                          <TkSelectItem key={image.value} value={image.value}>
                            {image.label}
                          </TkSelectItem>
                        ))}
                      </TkSelectGroup>
                    )}
                    <TkSelectItem value="custom">Custom Base Image</TkSelectItem>
                  </TkSelectContent>
                </TkSelect>
              </TkCardContent>
            </TkCard>
          )}

          {/* Parent Image Selection (for extend_existing mode) */}
          {formData.creation_mode === 'extend_existing' && (
            <TkCard>
              <TkCardContent className="pt-6">
                <TkLabel htmlFor="parent_image_id" className="text-base font-semibold mb-3 block">
                  Parent Image
                </TkLabel>
                <TkSelect
                  value={formData.parent_image_id}
                  onValueChange={(value) => {
                    setFormData({ ...formData, parent_image_id: value })
                    loadParentDockerfile(value)
                  }}
                  required
                >
                  <TkSelectTrigger id="parent_image_id">
                    <TkSelectValue placeholder="Select a parent image..." />
                  </TkSelectTrigger>
                  <TkSelectContent>
                    {groupedImages.map((scope) => (
                      <TkSelectGroup key={scope.name}>
                        <TkSelectLabel>{scope.label}</TkSelectLabel>
                        {scope.images.map((image) => (
                          <TkSelectItem key={image.id} value={image.id}>
                            {image.name} ({image.status})
                          </TkSelectItem>
                        ))}
                      </TkSelectGroup>
                    ))}
                  </TkSelectContent>
                </TkSelect>
                <p className="text-sm text-muted-foreground mt-2">
                  Select an existing build to extend
                </p>
              </TkCardContent>
            </TkCard>
          )}

          {/* Custom Base Image (when custom is selected) */}
          {formData.base_image === 'custom' && formData.creation_mode === 'from_base' && (
            <TkCard>
              <TkCardContent className="pt-6">
                <TkLabel htmlFor="custom_base_image" className="text-base font-semibold mb-3 block">
                  Custom Base Image
                </TkLabel>
                <TkInput
                  id="custom_base_image"
                  value={formData.custom_base_image}
                  onChange={(e) => setFormData({ ...formData, custom_base_image: e.target.value })}
                  placeholder="registry.thinkube.com/library/my-base:latest"
                  required
                />
              </TkCardContent>
            </TkCard>
          )}

          {/* Description */}
          <TkCard>
            <TkCardContent className="pt-6">
              <TkLabel htmlFor="description" className="text-base font-semibold mb-3 block">
                Description
              </TkLabel>
              <TkTextarea
                id="description"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Describe what this image is for..."
                className="h-24"
              />
            </TkCardContent>
          </TkCard>

          {/* Mark as Base Image */}
          <TkCard>
            <TkCardContent className="pt-6">
              <TkLabel className="text-base font-semibold mb-3 block">Image Options</TkLabel>
              <div className="flex items-center gap-2">
                <TkCheckbox
                  id="is_base"
                  checked={formData.is_base}
                  onCheckedChange={(checked) =>
                    setFormData({ ...formData, is_base: checked as boolean })
                  }
                />
                <TkLabel htmlFor="is_base" className="font-normal cursor-pointer">
                  Mark as base image (can be used as parent for other images)
                </TkLabel>
              </div>
            </TkCardContent>
          </TkCard>

          {/* Dockerfile Content */}
          <TkCard>
            <TkCardContent className="pt-6">
              <TkLabel htmlFor="dockerfile_content" className="text-base font-semibold mb-3 block">
                Dockerfile Content
              </TkLabel>
              <TkTextarea
                id="dockerfile_content"
                value={formData.dockerfile_content}
                onChange={(e) => setFormData({ ...formData, dockerfile_content: e.target.value })}
                placeholder="# Dockerfile will be auto-generated based on your selections"
                className="h-48 font-mono text-sm"
                required
              />
              <p className="text-sm text-muted-foreground mt-2">
                You can edit this later in code-server
              </p>
            </TkCardContent>
          </TkCard>

          <TkDialogFooter>
            <TkButton type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </TkButton>
            <TkButton type="submit" disabled={loading}>
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create Image
            </TkButton>
          </TkDialogFooter>
        </form>
      </TkDialogContent>
    </TkDialogRoot>
  )
}
