/*
 * Copyright 2025 Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import {
  TkDialogRoot,
  TkDialogContent,
  TkDialogHeader,
  TkDialogTitle,
  TkDialogFooter,
} from "thinkube-style/components/modals-overlays"
import { TkButton } from "thinkube-style/components/buttons-badges"
import { TkBadge } from "thinkube-style/components/buttons-badges"
import { TkCodeBlock } from "thinkube-style/components/feedback"
import { Lock } from "lucide-react"

interface HarborImage {
  id?: string
  name: string
  tag: string
  category: "core" | "custom" | "user"
  protected: boolean
  registry?: string
  repository?: string
  destination_url?: string
  source_url?: string
  description?: string
  mirror_date?: string
  last_synced?: string
  vulnerabilities?: {
    critical?: number
    high?: number
    medium?: number
    low?: number
  }
  metadata?: Record<string, unknown>
  size_bytes?: number
}

interface ViewImageModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  image: HarborImage | null
}

export function ViewImageModal({
  open,
  onOpenChange,
  image,
}: ViewImageModalProps) {
  const formatDate = (dateString?: string): string => {
    if (!dateString) return "Never"
    const date = new Date(dateString)
    return date.toLocaleDateString() + " " + date.toLocaleTimeString()
  }

  const formatSize = (bytes?: number): string => {
    if (!bytes) return "Unknown"
    const sizes = ["Bytes", "KB", "MB", "GB"]
    if (bytes === 0) return "0 Bytes"
    const i = Math.floor(Math.log(bytes) / Math.log(1024))
    return Math.round((bytes / Math.pow(1024, i)) * 100) / 100 + " " + sizes[i]
  }

  const getCategoryVariant = (
    category: string
  ): "default" | "success" | "warning" => {
    switch (category) {
      case "core":
        return "default"
      case "custom":
        return "success"
      case "user":
        return "warning"
      default:
        return "default"
    }
  }

  const close = () => {
    onOpenChange(false)
  }

  if (!image) return null

  return (
    <TkDialogRoot open={open} onOpenChange={onOpenChange}>
      <TkDialogContent className="max-w-3xl">
        <TkDialogHeader>
          <TkDialogTitle>Image Details</TkDialogTitle>
        </TkDialogHeader>

        <div className="space-y-6">
          {/* Basic Info */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-semibold text-foreground">
                Name
              </label>
              <p className="text-lg mt-1">{image.name}</p>
            </div>
            <div>
              <label className="text-sm font-semibold text-foreground">
                Tag
              </label>
              <p className="text-lg mt-1">
                <TkBadge variant="outline">{image.tag}</TkBadge>
              </p>
            </div>
          </div>

          {/* Category and Protection */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-semibold text-foreground">
                Category
              </label>
              <div className="mt-1">
                <TkBadge variant={getCategoryVariant(image.category)}>
                  {image.category}
                </TkBadge>
              </div>
            </div>
            <div>
              <label className="text-sm font-semibold text-foreground">
                Protection Status
              </label>
              {image.protected ? (
                <div className="flex items-center gap-2 mt-1">
                  <Lock className="h-5 w-5 text-success" />
                  <span>Protected from deletion</span>
                </div>
              ) : (
                <div className="mt-1 text-muted-foreground">Not protected</div>
              )}
            </div>
          </div>

          {/* URLs */}
          <div>
            <label className="text-sm font-semibold text-foreground">
              Registry URL
            </label>
            <TkCodeBlock className="mt-1">
              {image.destination_url ||
                `${image.registry}/${image.repository}:${image.tag}`}
            </TkCodeBlock>
          </div>

          {image.source_url && (
            <div>
              <label className="text-sm font-semibold text-foreground">
                Source URL
              </label>
              <TkCodeBlock className="mt-1">{image.source_url}</TkCodeBlock>
            </div>
          )}

          {/* Description */}
          {image.description && (
            <div>
              <label className="text-sm font-semibold text-foreground">
                Description
              </label>
              <p className="text-base mt-1">{image.description}</p>
            </div>
          )}

          {/* Timestamps */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-semibold text-foreground">
                Mirror Date
              </label>
              <p className="mt-1">{formatDate(image.mirror_date)}</p>
            </div>
            <div>
              <label className="text-sm font-semibold text-foreground">
                Last Synced
              </label>
              <p className="mt-1">{formatDate(image.last_synced)}</p>
            </div>
          </div>

          {/* Vulnerabilities */}
          {image.vulnerabilities &&
            Object.keys(image.vulnerabilities).length > 0 && (
              <div>
                <label className="text-sm font-semibold text-foreground">
                  Vulnerability Summary
                </label>
                <div className="flex flex-wrap gap-2 mt-1">
                  {image.vulnerabilities.critical !== undefined &&
                    image.vulnerabilities.critical > 0 && (
                      <TkBadge variant="destructive">
                        {image.vulnerabilities.critical} Critical
                      </TkBadge>
                    )}
                  {image.vulnerabilities.high !== undefined &&
                    image.vulnerabilities.high > 0 && (
                      <TkBadge variant="warning">
                        {image.vulnerabilities.high} High
                      </TkBadge>
                    )}
                  {image.vulnerabilities.medium !== undefined &&
                    image.vulnerabilities.medium > 0 && (
                      <TkBadge variant="default">
                        {image.vulnerabilities.medium} Medium
                      </TkBadge>
                    )}
                  {image.vulnerabilities.low !== undefined &&
                    image.vulnerabilities.low > 0 && (
                      <TkBadge variant="secondary">
                        {image.vulnerabilities.low} Low
                      </TkBadge>
                    )}
                </div>
              </div>
            )}

          {/* Metadata */}
          {image.metadata && Object.keys(image.metadata).length > 0 && (
            <div>
              <label className="text-sm font-semibold text-foreground">
                Additional Metadata
              </label>
              <TkCodeBlock className="mt-1">
                {JSON.stringify(image.metadata, null, 2)}
              </TkCodeBlock>
            </div>
          )}

          {/* Size */}
          {image.size_bytes && (
            <div>
              <label className="text-sm font-semibold text-foreground">
                Image Size
              </label>
              <p className="mt-1">{formatSize(image.size_bytes)}</p>
            </div>
          )}
        </div>

        <TkDialogFooter>
          <TkButton onClick={close}>Close</TkButton>
        </TkDialogFooter>
      </TkDialogContent>
    </TkDialogRoot>
  )
}
