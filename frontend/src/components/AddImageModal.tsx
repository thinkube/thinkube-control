import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
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
import { TkLabel } from 'thinkube-style/components/forms-inputs'
import { Loader2 } from 'lucide-react'
import { useHarborStore } from '@/stores/useHarborStore'
import { useToast } from '@/hooks/use-toast'

interface AddImageModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImageAdded?: (result: any) => void
}

interface FormData {
  source_url: string
  description: string
  auto_mirror: boolean
}

interface FormErrors {
  source_url: string
}

export function AddImageModal({
  open,
  onOpenChange,
  onImageAdded,
}: AddImageModalProps) {
  const router = useRouter()
  const { toast } = useToast()
  const { addImage, loading } = useHarborStore()

  const [form, setForm] = useState<FormData>({
    source_url: '',
    description: '',
    auto_mirror: true,
  })

  const [errors, setErrors] = useState<FormErrors>({
    source_url: '',
  })

  const isValid = form.source_url && !errors.source_url

  // Validate source URL
  useEffect(() => {
    if (!form.source_url) {
      setErrors({ source_url: '' })
      return
    }

    const urlPattern = /^[a-zA-Z0-9.-]+\/[a-zA-Z0-9.\/_-]+:[a-zA-Z0-9._-]+$/
    const simplePattern = /^[a-zA-Z0-9.\/_-]+:[a-zA-Z0-9._-]+$/

    if (!urlPattern.test(form.source_url) && !simplePattern.test(form.source_url)) {
      setErrors({ source_url: 'Invalid image URL format. Expected: registry/path:tag' })
    } else {
      setErrors({ source_url: '' })
    }
  }, [form.source_url])

  const handleClose = () => {
    onOpenChange(false)
    resetForm()
  }

  const resetForm = () => {
    setForm({
      source_url: '',
      description: '',
      auto_mirror: true,
    })
    setErrors({
      source_url: '',
    })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!isValid) return

    try {
      const result = await addImage({
        source_url: form.source_url,
        description: form.description,
        auto_mirror: form.auto_mirror,
      })

      if (result.deployment_id) {
        handleClose()
        router.push(`/harbor-images/mirror/${result.deployment_id}`)
      } else if (result.job) {
        toast({
          title: 'Success',
          description: `Image added successfully! Mirror job started with ID: ${result.job.id}`,
        })
      } else {
        toast({
          title: 'Success',
          description: 'Image added to inventory successfully!',
        })
      }

      onImageAdded?.(result)
      handleClose()
    } catch (error: any) {
      const errorMessage = error.response?.data?.detail || error.message || 'Failed to add image'
      toast({
        title: 'Error',
        description: errorMessage,
        variant: 'destructive',
      })
    }
  }

  return (
    <TkDialogRoot open={open} onOpenChange={onOpenChange}>
      <TkDialogContent className="max-w-2xl">
        <TkDialogHeader>
          <TkDialogTitle>Add Image to Mirror</TkDialogTitle>
        </TkDialogHeader>

        <form onSubmit={handleSubmit}>
          <div className="space-y-4">
            {/* Source URL */}
            <div className="space-y-2">
              <TkLabel htmlFor="source_url">
                Source Image URL <span className="text-destructive">*</span>
              </TkLabel>
              <TkInput
                id="source_url"
                type="text"
                placeholder="e.g., docker.io/library/nginx:latest"
                value={form.source_url}
                onChange={(e) => setForm({ ...form, source_url: e.target.value })}
                className={errors.source_url ? 'border-destructive' : ''}
                required
              />
              {errors.source_url && (
                <p className="text-sm text-destructive">{errors.source_url}</p>
              )}
              <p className="text-sm text-muted-foreground">
                Examples: docker.io/library/alpine:latest, quay.io/prometheus/prometheus:latest
              </p>
            </div>

            {/* Description */}
            <div className="space-y-2">
              <TkLabel htmlFor="description">Description</TkLabel>
              <TkTextarea
                id="description"
                placeholder="Brief description of what this image is used for"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="h-24"
              />
            </div>

            {/* Auto Mirror */}
            <div className="space-y-2">
              <TkLabel>Mirror Options</TkLabel>
              <div className="flex items-center gap-2">
                <TkCheckbox
                  id="auto_mirror"
                  checked={form.auto_mirror}
                  onCheckedChange={(checked) =>
                    setForm({ ...form, auto_mirror: checked as boolean })
                  }
                />
                <TkLabel htmlFor="auto_mirror" className="font-normal cursor-pointer">
                  Start mirroring immediately
                </TkLabel>
              </div>
              <p className="text-sm text-muted-foreground">
                If checked, the image will be mirrored to Harbor right away. Otherwise, it will only be added to the inventory.
              </p>
            </div>
          </div>

          <TkDialogFooter className="mt-6">
            <TkButton
              type="button"
              variant="outline"
              onClick={handleClose}
              disabled={loading}
            >
              Cancel
            </TkButton>
            <TkButton type="submit" disabled={loading || !isValid}>
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {form.auto_mirror ? 'Add & Mirror' : 'Add to Inventory'}
            </TkButton>
          </TkDialogFooter>
        </form>
      </TkDialogContent>
    </TkDialogRoot>
  )
}
