/*
 * Copyright 2025 Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { Check, Download, Trash2 } from "lucide-react"
import {
  TkCard,
  TkCardContent,
} from "thinkube-style/components/cards-data"
import {
  TkBadge,
  TkButton,
} from "thinkube-style/components/buttons-badges"
import { TkWarningAlert } from "thinkube-style/components/feedback"

interface Component {
  display_name: string
  description: string
  icon: string
  installed: boolean
  requirements?: string[]
  requirements_met: boolean
  missing_requirements: string[]
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
  const isMissingRequirement = (req: string) => {
    return component.missing_requirements?.includes(req)
  }

  return (
    <TkCard className="h-full">
      <TkCardContent className="flex flex-col h-full">
        {/* Header with icon and title */}
        <div className="flex items-start space-x-3">
          <img
            src={component.icon}
            alt={component.display_name}
            className="w-12 h-12"
          />
          <div className="flex-1">
            <h3 className="text-lg font-semibold">{component.display_name}</h3>
            <div className="flex items-center space-x-2 mt-1">
              {/* Installation status badge */}
              {component.installed ? (
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

        {/* Description */}
        <p className="text-sm text-muted-foreground mt-3">
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
        {!component.requirements_met &&
          component.missing_requirements.length > 0 && (
            <div className="mt-3">
              <TkWarningAlert>
                <span className="text-xs">
                  Missing: {component.missing_requirements.join(", ")}
                </span>
              </TkWarningAlert>
            </div>
          )}

        {/* Actions */}
        <div className="flex justify-end mt-auto pt-4">
          {!component.installed ? (
            <TkButton
              onClick={() => onInstall(component)}
              disabled={!component.requirements_met && !allowForceInstall}
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
