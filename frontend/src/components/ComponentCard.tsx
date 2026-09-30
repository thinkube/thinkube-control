/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { Download, Trash2 } from "lucide-react"
import { TkButton } from "thinkube-style/components/buttons-badges"
import { TkBrandIcon } from "thinkube-style/components/brand-icons"
import { AppCard, type AppCardState } from "./AppCard"

/** An optional component as GET /optional-components returns it. */
export interface OptionalComponent {
  name: string
  display_name: string
  description: string
  category: string
  icon: string
  installed: boolean
  activity: 'queued' | 'installing' | 'uninstalling' | null
  component_version: string | null
  requirements_met: boolean
  missing_requirements: string[]
}

interface ComponentCardProps {
  component: OptionalComponent
  onInstall: (component: OptionalComponent) => void
  onUninstall: (component: OptionalComponent) => void
}

const ACTIVITY_LABEL = {
  queued: 'Queued',
  installing: 'Installing',
  uninstalling: 'Uninstalling',
}

export function ComponentCard({
  component,
  onInstall,
  onUninstall,
}: ComponentCardProps) {
  const isInstalled = component.installed
  const missingRequirements = component.missing_requirements

  // A queued run for the component counts as busy: it will run, so the card offers no second action.
  const busy = !!component.activity
  const missing = !isInstalled && !component.requirements_met

  // The bar and the line under the name carry the state; the description
  // takes the line when there is no state to report.
  let state: AppCardState
  let statusLabel: string
  let subtitle: string
  if (component.activity) {
    state = 'idle'
    statusLabel = ACTIVITY_LABEL[component.activity]
    subtitle = statusLabel
  } else if (isInstalled) {
    state = 'healthy'
    statusLabel = 'Installed'
    subtitle = 'Installed'
  } else if (missing) {
    state = 'unknown'
    statusLabel = 'Requirements missing'
    subtitle = `Needs ${missingRequirements.join(', ')}`
  } else {
    state = 'available'
    statusLabel = 'Not installed'
    subtitle = component.description
  }

  return (
    <AppCard
      state={state}
      statusLabel={statusLabel}
      srDescription="Optional component."
      version={component.component_version}
      icon={
        <TkBrandIcon
          icon={(component.icon ?? '').replace('/icons/', '').replace('.svg', '')}
          alt={component.display_name}
          size={40}
          color="var(--service-type-optional)"
        />
      }
      name={component.display_name}
      subtitle={subtitle}
      tooltip={<p>{component.description}</p>}
      actions={
        !isInstalled ? (
          <TkButton
            size="sm"
            className="h-7 px-2.5 text-xs"
            onClick={() => onInstall(component)}
            disabled={busy || !component.requirements_met}
          >
            <Download className="h-3.5 w-3.5" />
            Install
          </TkButton>
        ) : (
          <TkButton
            size="sm"
            intent="secondary"
            className="h-7 px-2.5 text-xs text-destructive hover:bg-destructive hover:text-destructive-foreground"
            onClick={() => onUninstall(component)}
            disabled={busy}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Uninstall
          </TkButton>
        )
      }
    />
  )
}
