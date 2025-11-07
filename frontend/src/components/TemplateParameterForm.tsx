/*
 * Copyright 2025 Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useRef, useMemo } from 'react'
import { TkInput } from 'thinkube-style/components/forms-inputs'
import { TkLabel } from 'thinkube-style/components/forms-inputs'
import { TkSelect, TkSelectTrigger, TkSelectContent, TkSelectItem, TkSelectValue } from 'thinkube-style/components/forms-inputs'
import { TkCheckbox } from 'thinkube-style/components/forms-inputs'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkWarningAlert } from 'thinkube-style/components/feedback'
import { TkLoader } from 'thinkube-style/components/feedback'
import { TkSeparator } from 'thinkube-style/components/utilities'
import api from '../lib/axios'
import { AlertTriangle } from 'lucide-react'

interface TemplateParameter {
  name: string
  type: 'str' | 'int' | 'bool' | 'choice'
  description?: string
  default?: string | number | boolean
  placeholder?: string
  pattern?: string
  required?: boolean
  minLength?: number
  maxLength?: number
  min?: number
  max?: number
  choices?: string[]
  group?: string
  order?: number
}

interface ParameterGroup {
  name: string
  parameters: TemplateParameter[]
}

interface TemplateParameterFormProps {
  parameters?: TemplateParameter[]
  modelValue?: Record<string, string | number | boolean>
  onUpdate?: (value: Record<string, string | number | boolean>) => void
  onValidationChange?: (validation: { isValid: boolean; fieldName: string }) => void
}

interface NameValidation {
  valid: boolean
  message: string
  class: string
  messageClass: string
}

interface ExistingService {
  type: string
  [key: string]: unknown
}

export function TemplateParameterForm({
  parameters = [],
  modelValue = {},
  onUpdate,
  onValidationChange,
}: TemplateParameterFormProps) {
  // Local form data
  const [formData, setFormData] = useState<Record<string, string | number | boolean>>({
    project_name: '',
    project_description: '',
    ...modelValue,
  })

  // Name validation state
  const [checkingName, setCheckingName] = useState(false)
  const [nameValidation, setNameValidation] = useState<NameValidation>({
    valid: true,
    message: '',
    class: '',
    messageClass: '',
  })
  const [showOverwriteConfirm, setShowOverwriteConfirm] = useState(false)
  const [existingServiceInfo, setExistingServiceInfo] = useState<ExistingService | null>(null)
  const nameCheckTimeout = useRef<NodeJS.Timeout | null>(null)
  const prevModelValueRef = useRef<Record<string, string | number | boolean> | null>(null)

  // Group parameters by their group field
  const parameterGroups = useMemo<ParameterGroup[]>(() => {
    const groups: Record<string, ParameterGroup> = {}

    // Add parameters to groups
    parameters.forEach((param) => {
      const groupName = param.group || 'default'
      if (!groups[groupName]) {
        groups[groupName] = {
          name: groupName,
          parameters: [],
        }
      }
      groups[groupName].parameters.push(param)
    })

    // Sort parameters within groups by order
    Object.values(groups).forEach((group) => {
      group.parameters.sort((a, b) => (a.order || 999) - (b.order || 999))
    })

    // Convert to array and sort groups (default first, then alphabetical)
    return Object.values(groups).sort((a, b) => {
      if (a.name === 'default') return -1
      if (b.name === 'default') return 1
      return a.name.localeCompare(b.name)
    })
  }, [parameters])

  // Update parent when form data changes
  const updateValue = (field: string, value: string | number | boolean) => {
    const newData = { ...formData, [field]: value }
    setFormData(newData)
    onUpdate?.(newData)

    // Emit validation state for project_name
    if (field === 'project_name' || field === '_overwrite_confirmed') {
      onValidationChange?.({
        isValid: nameValidation.valid || !!formData._overwrite_confirmed,
        fieldName: 'project_name',
      })
    }
  }

  // Initialize formData only once on mount with modelValue
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    // Only run on mount when prevModelValueRef is null
    // We intentionally don't include modelValue in deps to prevent sync loops
    if (prevModelValueRef.current === null) {
      setFormData({
        project_name: '',
        project_description: '',
        ...modelValue,
      })
      prevModelValueRef.current = { ...modelValue }
    }
  }, [])

  // Validate project name against existing services
  const validateProjectName = async () => {
    const name = formData.project_name as string
    if (!name) return

    // Check reserved names
    const reservedNames = [
      'keycloak',
      'gitlab',
      'harbor',
      'argocd',
      'argo-workflows',
      'prometheus',
      'postgres',
      'postgresql',
      'redis',
      'seaweedfs',
      'gitea',
      'devpi',
      'awx',
      'thinkube-control',
      'code-server',
      'mlflow',
      'zitadel',
    ]

    if (reservedNames.includes(name)) {
      setNameValidation({
        valid: false,
        message: `"${name}" is a reserved system service name`,
        class: 'border-destructive',
        messageClass: 'text-destructive',
      })
      return
    }

    setCheckingName(true)
    try {
      const { data } = await api.post('/services/check-name', {
        name: name,
        type: 'user', // User applications
      })

      if (data.available) {
        setNameValidation({
          valid: true,
          message: '✓ Name is available',
          class: 'border-success',
          messageClass: 'text-success',
        })
      } else {
        setNameValidation({
          valid: false,
          message: data.reason || 'Name is not available',
          class: 'border-warning',
          messageClass: 'text-warning',
        })

        // If it's a user app, offer to overwrite
        if (data.existing_service && data.existing_service.type === 'user') {
          setExistingServiceInfo(data.existing_service)
          setShowOverwriteConfirm(true)
        }
      }
    } catch (error) {
      console.error('Name validation error:', error)
      setNameValidation({
        valid: true, // Don't block on API errors
        message: '',
        class: '',
        messageClass: '',
      })
    } finally {
      setCheckingName(false)
    }
  }

  // Handle project name change with debounced validation
  const handleProjectNameChange = (value: string) => {
    updateValue('project_name', value)

    // Clear existing timeout
    if (nameCheckTimeout.current) {
      clearTimeout(nameCheckTimeout.current)
    }

    // Reset validation state
    setShowOverwriteConfirm(false)
    setNameValidation({
      valid: true,
      message: '',
      class: '',
      messageClass: '',
    })

    // Validate format first
    if (value && !value.match(/^[a-z][a-z0-9-]*$/)) {
      setNameValidation({
        valid: false,
        message:
          'Must start with a letter and contain only lowercase letters, numbers, and hyphens',
        class: 'border-destructive',
        messageClass: 'text-destructive',
      })
      return
    }

    // Debounce the API check
    if (value) {
      nameCheckTimeout.current = setTimeout(() => {
        validateProjectName()
      }, 500)
    }
  }

  // Handle overwrite confirmation
  const confirmOverwrite = () => {
    setNameValidation({
      valid: true,
      message: '⚠️ Will replace existing application',
      class: 'border-warning',
      messageClass: 'text-warning',
    })
    setShowOverwriteConfirm(false)

    // Add a flag to indicate overwrite is confirmed
    updateValue('_overwrite_confirmed', true)
  }

  // Handle overwrite cancellation
  const cancelOverwrite = () => {
    setFormData({ ...formData, project_name: '' })
    updateValue('project_name', '')
    updateValue('_overwrite_confirmed', false)
    setShowOverwriteConfirm(false)
    setNameValidation({
      valid: true,
      message: '',
      class: '',
      messageClass: '',
    })
  }

  // Format label from parameter name
  const formatLabel = (param: TemplateParameter): string => {
    if (param.description) {
      return param.description
    }
    // Convert snake_case to Title Case
    return param.name
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

  return (
    <div className="space-y-6">
      {/* Standard fields that are always present */}
      <div className="space-y-2">
        <TkLabel htmlFor="project-name">Project Name</TkLabel>
        <div className="relative">
          <TkInput
            id="project-name"
            value={(formData.project_name as string) || ''}
            type="text"
            placeholder="my-awesome-app"
            className={nameValidation.class}
            pattern="[a-z][a-z0-9-]*"
            required
            onChange={(e) => handleProjectNameChange(e.target.value)}
            onBlur={() => validateProjectName()}
          />
          {checkingName && (
            <div className="absolute right-3 top-3">
              <TkLoader size="sm" />
            </div>
          )}
        </div>
        {nameValidation.message ? (
          <p className={`text-sm ${nameValidation.messageClass}`}>{nameValidation.message}</p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Lowercase letters, numbers, and hyphens only
          </p>
        )}

        {/* Confirmation dialog for overwriting */}
        {showOverwriteConfirm && (
          <TkWarningAlert title="Application Already Exists">
            <p>An application named &quot;{formData.project_name}&quot; already exists.</p>
            <p>Do you want to replace it? This will delete the existing application.</p>
            <div className="mt-4 flex gap-2">
              <TkButton
                size="sm"
                variant="default"
                className="bg-warning hover:bg-warning/90"
                onClick={confirmOverwrite}
              >
                Yes, Replace
              </TkButton>
              <TkButton size="sm" variant="ghost" onClick={cancelOverwrite}>
                Cancel
              </TkButton>
            </div>
          </TkWarningAlert>
        )}
      </div>

      <div className="space-y-2">
        <TkLabel htmlFor="project-description">Project Description</TkLabel>
        <TkInput
          id="project-description"
          value={(formData.project_description as string) || ''}
          type="text"
          placeholder="Brief description of your project"
          onChange={(e) => updateValue('project_description', e.target.value)}
        />
      </div>

      {/* Group parameters by group */}
      {parameterGroups.map((group) => (
        <div key={group.name} className="space-y-4">
          {group.name !== 'default' && (
            <div className="flex items-center gap-4">
              <TkSeparator className="flex-1" />
              <span className="text-sm font-medium text-muted-foreground">{group.name}</span>
              <TkSeparator className="flex-1" />
            </div>
          )}

          {/* Render each parameter based on type */}
          {group.parameters.map((param) => (
            <div key={param.name}>
              {/* String input */}
              {param.type === 'str' && (
                <div className="space-y-2">
                  <TkLabel htmlFor={`param-${param.name}`}>{formatLabel(param)}</TkLabel>
                  <TkInput
                    id={`param-${param.name}`}
                    value={(formData[param.name] as string) || (param.default as string) || ''}
                    type="text"
                    placeholder={param.placeholder || ''}
                    pattern={param.pattern}
                    required={param.required}
                    minLength={param.minLength}
                    maxLength={param.maxLength}
                    onChange={(e) => updateValue(param.name, e.target.value)}
                  />
                  {param.pattern && (
                    <p className="text-sm text-muted-foreground">Format: {param.pattern}</p>
                  )}
                </div>
              )}

              {/* Integer input */}
              {param.type === 'int' && (
                <div className="space-y-2">
                  <TkLabel htmlFor={`param-${param.name}`}>{formatLabel(param)}</TkLabel>
                  <TkInput
                    id={`param-${param.name}`}
                    value={(formData[param.name] as number) || (param.default as number) || 0}
                    type="number"
                    placeholder={param.placeholder || ''}
                    min={param.min}
                    max={param.max}
                    required={param.required}
                    onChange={(e) => updateValue(param.name, parseInt(e.target.value))}
                  />
                  {(param.min !== undefined || param.max !== undefined) && (
                    <p className="text-sm text-muted-foreground">
                      {param.min !== undefined ? `Min: ${param.min}` : ''}
                      {param.min !== undefined && param.max !== undefined ? ', ' : ''}
                      {param.max !== undefined ? `Max: ${param.max}` : ''}
                    </p>
                  )}
                </div>
              )}

              {/* Boolean checkbox */}
              {param.type === 'bool' && (
                <div className="flex items-center justify-between space-x-2">
                  <TkLabel htmlFor={`param-${param.name}`} className="cursor-pointer">
                    {formatLabel(param)}
                  </TkLabel>
                  <TkCheckbox
                    id={`param-${param.name}`}
                    checked={
                      formData[param.name] !== undefined
                        ? (formData[param.name] as boolean)
                        : (param.default as boolean)
                    }
                    onCheckedChange={(checked) => updateValue(param.name, checked as boolean)}
                  />
                </div>
              )}

              {/* Choice dropdown */}
              {param.type === 'choice' && (
                <div className="space-y-2">
                  <TkLabel htmlFor={`param-${param.name}`}>{formatLabel(param)}</TkLabel>
                  <TkSelect
                    value={(formData[param.name] as string) || (param.default as string) || ''}
                    onValueChange={(value) => updateValue(param.name, value)}
                  >
                    <TkSelectTrigger id={`param-${param.name}`}>
                      <TkSelectValue placeholder={`Select ${formatLabel(param)}`} />
                    </TkSelectTrigger>
                    <TkSelectContent>
                      {param.choices?.map((choice) => (
                        <TkSelectItem key={choice} value={choice}>
                          {choice}
                        </TkSelectItem>
                      ))}
                    </TkSelectContent>
                  </TkSelect>
                </div>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
