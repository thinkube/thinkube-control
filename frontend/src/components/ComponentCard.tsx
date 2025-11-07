/*
 * Copyright 2025 Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { Check, Download, Trash2 } from "lucide-react"
import {
  TkCard,
  TkCardHeader,
  TkCardContent,
} from "thinkube-style/components/cards-data"
import {
  TkBadge,
  TkButton,
} from "thinkube-style/components/buttons-badges"
import { TkWarningAlert } from "thinkube-style/components/feedback"
import { TkBrandIcon } from "thinkube-style/components/brand-icons"

interface Component {
  display_name: string
  description: string
  icon: string
  is_installed?: boolean
  installed?: boolean  // Legacy support
  requirements?: string[]
  requirements_met?: boolean
  missing_requirements?: string[]
}

interface ComponentCardProps {
  component: Component
  allowForceInstall?: boolean
  onInstall: (component: Component) => void
  onUninstall: (component: Component) => void
}

export function ComponentCard({
  component,
  allowForceInstall = false,
  onInstall,
  onUninstall,
}: ComponentCardProps) {
  // Support both installed and is_installed field names
  const isInstalled = component.is_installed ?? component.installed ?? false
  const requirementsMet = component.requirements_met ?? true  // Default to true if not specified
  const missingRequirements = component.missing_requirements ?? []

  console.log('🔍 DEBUG ComponentCard render:', {
    name: component.display_name,
    isInstalled,
    requirementsMet,
    missingRequirements,
    buttonDisabled: !requirementsMet && !allowForceInstall
  })

  const isMissingRequirement = (req: string) => {
    return missingRequirements?.includes(req)
  }

  return (
    <TkCard className="h-full flex flex-col">
      <TkCardHeader className="pb-3">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0">
            <TkBrandIcon
              icon={component.icon.replace('/icons/', '').replace('.svg', '')}
              alt={component.display_name}
              size={20}
            />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-semibold mb-2">{component.display_name}</h3>
            <div className="flex items-center gap-2">
              {/* Installation status badge */}
              {isInstalled ? (
                <TkBadge variant="success" className="gap-1">
                  <Check className="w-3 h-3" />
                  Installed
                </TkBadge>
              ) : (
                <TkBadge variant="outline">Not Installed</TkBadge>
              )}
            </div>
          </div>
        </div>
      </TkCardHeader>

      <TkCardContent className="flex flex-col flex-1">
        {/* Description */}
        <p className="text-sm text-muted-foreground mb-3">
          {component.description}
        </p>

        {/* Requirements */}
        {component.requirements && component.requirements.length > 0 && (
          <div className="mt-3">
            <div className="text-xs font-semibold text-muted-foreground mb-1">
              Requirements:
            </div>
            <div className="flex flex-wrap gap-1">
              {component.requirements.map((req) => (
                <TkBadge
                  key={req}
                  variant={
                    isMissingRequirement(req) ? "destructive" : "outline"
                  }
                  className="text-xs"
                >
                  {req}
                </TkBadge>
              ))}
            </div>
          </div>
        )}

        {/* Missing requirements alert */}
        {!requirementsMet &&
          missingRequirements.length > 0 && (
            <div className="mt-3">
              <TkWarningAlert>
                <span className="text-xs">
                  Missing: {missingRequirements.join(", ")}
                </span>
              </TkWarningAlert>
            </div>
          )}

        {/* Actions */}
        <div className="flex justify-end mt-auto pt-4">
          {!isInstalled ? (
            <TkButton
              onClick={() => {
                console.log('🔍 DEBUG: Install button clicked for:', component.display_name)
                onInstall(component)
              }}
              disabled={!requirementsMet && !allowForceInstall}
              size="sm"
              className="gap-2"
            >
              <Download className="w-4 h-4" />
              Install
            </TkButton>
          ) : (
            <TkButton
              onClick={() => onUninstall(component)}
              variant="destructive"
              size="sm"
              className="gap-2"
            >
              <Trash2 className="w-4 h-4" />
              Uninstall
            </TkButton>
          )}
        </div>
      </TkCardContent>
    </TkCard>
  )
}
