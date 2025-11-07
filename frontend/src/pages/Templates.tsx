import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent, TkCardFooter } from 'thinkube-style/components/cards-data'
import { TkInput } from 'thinkube-style/components/forms-inputs'
import { TkInfoAlert, TkErrorAlert } from 'thinkube-style/components/feedback'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TemplateParameterForm } from '../components/TemplateParameterForm'
import { PlaybookExecutor } from '../components/PlaybookExecutor'
import api from '../lib/axios'

interface TemplateInfo {
  name: string
  description: string
  owner: string
}

interface TemplateMetadata {
  metadata: {
    description?: string
  }
  parameters: Array<{
    name: string
    type: string
    default?: any
    description?: string
    required?: boolean
  }>
}

interface DeployConfig {
  project_name: string
  project_description: string
  _overwrite_confirmed?: boolean
  [key: string]: any
}

interface PlaybookExecutorHandle {
  startExecution: (wsPath: string) => void
}

export default function Templates() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [showDeployForm, setShowDeployForm] = useState(false)
  const [templateUrl, setTemplateUrl] = useState('')
  const [templateInfo, setTemplateInfo] = useState<TemplateInfo | null>(null)
  const [templateMetadata, setTemplateMetadata] = useState<TemplateMetadata | null>(null)
  const [loadingMetadata, setLoadingMetadata] = useState(false)
  const [manualTemplateUrl, setManualTemplateUrl] = useState('')
  const [isDeploying, setIsDeploying] = useState(false)
  const [deploymentId, setDeploymentId] = useState<string | null>(null)
  const [deploymentFailed, setDeploymentFailed] = useState(false)
  const [deployConfig, setDeployConfig] = useState<DeployConfig>({
    project_name: '',
    project_description: ''
  })

  const playbookExecutorRef = useRef<PlaybookExecutorHandle>(null)
  const isLoadingRef = useRef(false) // Prevent concurrent loads

  // Compute domain name
  const domainName = typeof window !== 'undefined'
    ? window.location.hostname.replace('control.', '')
    : 'thinkube.com'

  // Check if config is valid
  const isValidConfig =
    templateMetadata &&
    deployConfig.project_name &&
    /^[a-z][a-z0-9-]*$/.test(deployConfig.project_name)

  const isValidUrl = (url: string): boolean => {
    try {
      const u = new URL(url)
      return u.hostname === 'github.com' && u.pathname.split('/').length >= 3
    } catch {
      return false
    }
  }

  // Load template information - must be called with explicit URL parameter
  const loadTemplate = useCallback(async (url: string) => {
    if (!isValidUrl(url)) {
      console.error('Invalid URL provided to loadTemplate:', url)
      return
    }

    // Prevent concurrent calls
    if (isLoadingRef.current) {
      console.log('Already loading template, skipping duplicate call')
      return
    }

    isLoadingRef.current = true
    setTemplateUrl(url)
    setShowDeployForm(true)
    setLoadingMetadata(true)
    setTemplateMetadata(null)

    // Extract repo info from URL
    const parts = url.split('/')
    const owner = parts[3]
    const repo = parts[4]

    setTemplateInfo({
      name: repo,
      description: 'Loading template information...',
      owner: owner
    })

    // Try to fetch template metadata from our API
    try {
      const token = localStorage.getItem('access_token')
      const response = await api.get('/templates/metadata', {
        params: { template_url: url },
        headers: { Authorization: `Bearer ${token}` }
      })

      if (response.data) {
        setTemplateMetadata(response.data)
        setTemplateInfo(prev => prev ? {
          ...prev,
          description: response.data.metadata.description || 'Template ready'
        } : null)

        // Initialize deployConfig with any defaults from parameters
        const defaultValues: Record<string, any> = {}
        response.data.parameters.forEach((param: any) => {
          if (param.default !== undefined && param.default !== null) {
            defaultValues[param.name] = param.default
          }
        })

        setDeployConfig({
          project_name: '',
          project_description: '',
          ...defaultValues
        })
      }
    } catch (e) {
      console.error('Failed to fetch template metadata:', e)
      // No template.yaml found - this is an error
      setTemplateInfo(prev => prev ? {
        ...prev,
        description: 'Invalid template - missing template.yaml'
      } : null)
      setTemplateMetadata(null)
    } finally {
      setLoadingMetadata(false)
      isLoadingRef.current = false
    }
  }, []) // No dependencies - function never changes

  // Check for deploy parameter on mount
  useEffect(() => {
    const deployUrl = searchParams.get('deploy')
    if (deployUrl && isValidUrl(deployUrl)) {
      // Clear query parameter to prevent re-triggering
      setSearchParams({}, { replace: true })
      // Load template with URL from query param
      loadTemplate(deployUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []) // Only run once on mount, loadTemplate is stable

  // Select a template from the gallery
  const selectTemplate = (url: string) => {
    if (!isValidUrl(url)) {
      console.error('Invalid template URL:', url)
      return
    }
    // Pass URL directly to avoid state timing issues
    loadTemplate(url)
  }

  // Cancel deployment
  const cancelDeploy = () => {
    setShowDeployForm(false)
    setTemplateUrl('')
    setTemplateInfo(null)
    setTemplateMetadata(null)
    setLoadingMetadata(false)
    setDeploymentFailed(false)
    setDeployConfig({
      project_name: '',
      project_description: ''
    })
  }

  // Handle deployment completion
  const handleDeploymentComplete = (result: any) => {
    setIsDeploying(false)

    if (result.status === 'success') {
      console.log('Deployment completed successfully')
    } else if (result.status === 'error') {
      setDeploymentFailed(true)
      console.error('Deployment failed:', result.message)
    }
  }

  // Deploy the template
  const handleDeployTemplate = async () => {
    if (!isValidConfig || isDeploying) return

    setIsDeploying(true)
    setDeploymentFailed(false)

    try {
      // Deploy template asynchronously
      const response = await api.post('/templates/deploy-async', {
        template_url: templateUrl,
        template_name: deployConfig.project_name,
        variables: {
          ...deployConfig,
          domain_name: domainName,
          author_name: 'Thinkube User',
          author_email: `user@${domainName}`
        }
      })

      // Check if there's a conflict that requires confirmation
      if (response.data.status === 'conflict' && response.data.requires_confirmation) {
        setIsDeploying(false)

        // Show confirmation dialog
        const confirmed = confirm(
          `${response.data.message}\n\nDo you want to overwrite the existing application?`
        )

        if (confirmed) {
          // Retry with overwrite flag
          setIsDeploying(true)
          const retryResponse = await api.post('/templates/deploy-async', {
            template_url: templateUrl,
            template_name: deployConfig.project_name,
            variables: {
              ...deployConfig,
              domain_name: domainName,
              author_name: 'Thinkube User',
              author_email: `user@${domainName}`,
              _overwrite_confirmed: true
            }
          })

          setDeploymentId(retryResponse.data.deployment_id)

          // Start PlaybookExecutor with WebSocket URL
          if (retryResponse.data.websocket_url) {
            playbookExecutorRef.current?.startExecution(`/api/v1${retryResponse.data.websocket_url}`)
          } else {
            playbookExecutorRef.current?.startExecution(`/api/v1/ws/deployment/${retryResponse.data.deployment_id}`)
          }
        } else {
          // User cancelled
          console.log('Deployment cancelled by user')
        }
        return
      }

      setDeploymentId(response.data.deployment_id)

      // Start PlaybookExecutor with WebSocket URL
      if (response.data.websocket_url) {
        playbookExecutorRef.current?.startExecution(`/api/v1${response.data.websocket_url}`)
      } else {
        playbookExecutorRef.current?.startExecution(`/api/v1/ws/deployment/${response.data.deployment_id}`)
      }

    } catch (error: any) {
      console.error('Failed to deploy template:', error)
      alert(`Failed to deploy template: ${error.response?.data?.detail || error.message}`)
      setIsDeploying(false)
    }
  }

  return (
    <TkPageWrapper>
      <div className="prose prose-lg mb-8">
        <h1>Templates</h1>
        <p className="lead">
          Deploy pre-configured application templates to your Thinkube cluster
        </p>
      </div>

      {/* Template Deployment Form */}
      {showDeployForm && (
        <TkCard className="mb-8">
          <TkCardHeader>
            <TkCardTitle>Deploy Template</TkCardTitle>
          </TkCardHeader>
          <TkCardContent className="space-y-6">
            {/* Template Info */}
            {templateInfo && (
              <TkInfoAlert title={templateInfo.name}>
                <p>{templateInfo.description}</p>
                <a
                  href={templateUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary text-sm hover:underline"
                >
                  {templateUrl}
                </a>
              </TkInfoAlert>
            )}

            {/* Loading template metadata */}
            {loadingMetadata && (
              <div className="flex items-center justify-center py-12"> {/* @allowed-inline */}
                <div className="text-center">
                  <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto mb-4" />
                  <p>Loading template configuration...</p>
                </div>
              </div>
            )}

            {/* Dynamic form based on template.yaml */}
            {!loadingMetadata && templateMetadata && (
              <TemplateParameterForm
                parameters={templateMetadata.parameters}
                modelValue={deployConfig}
                onUpdate={setDeployConfig}
              />
            )}

            {/* No template.yaml found */}
            {!loadingMetadata && !templateMetadata && (
              <TkErrorAlert title="Invalid Template">
                <p>This template does not have a template.yaml file.</p>
                <p>All Thinkube templates must include a template.yaml manifest file.</p>
              </TkErrorAlert>
            )}
          </TkCardContent>

          {/* Action Buttons */}
          <TkCardFooter className="flex justify-end gap-2">
            <TkButton
              variant="ghost"
              onClick={cancelDeploy}
            >
              Cancel
            </TkButton>
            <TkButton
              variant="default"
              disabled={!isValidConfig || isDeploying}
              onClick={handleDeployTemplate}
            >
              {isDeploying && (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              )}
              {isDeploying ? 'Deploying...' : 'Deploy Template'}
            </TkButton>
          </TkCardFooter>
        </TkCard>
      )}

      {/* Playbook Executor */}
      <PlaybookExecutor
        ref={playbookExecutorRef}
        title={`Deploying ${deployConfig.project_name}`}
        successMessage={`Deployment complete! Your application will be available at https://${deployConfig.project_name}.${domainName}`}
        onComplete={handleDeploymentComplete}
      />

      {/* Manual Template URL */}
      {!showDeployForm && (
        <TkCard className="mb-8">
          <TkCardHeader>
            <TkCardTitle>Deploy from GitHub</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <p className="mb-4">Enter a GitHub repository URL to deploy a template</p>

            <div className="space-y-2">
              <label
                htmlFor="template-url"
                className="text-sm font-medium"
              >
                Template Repository URL
              </label>
              <TkInput
                id="template-url"
                value={manualTemplateUrl}
                onChange={(e) => setManualTemplateUrl(e.target.value)}
                type="url"
                placeholder="https://github.com/thinkube/tkt-webapp-vue-fastapi"
              />
            </div>
          </TkCardContent>
          <TkCardFooter className="flex justify-end">
            <TkButton
              variant="default"
              disabled={!isValidUrl(manualTemplateUrl)}
              onClick={() => loadTemplate(manualTemplateUrl)}
            >
              Load Template
            </TkButton>
          </TkCardFooter>
        </TkCard>
      )}

      {/* Available Templates */}
      <div className="mb-4">
        <h2 className="text-2xl font-bold mb-4">
          Available Templates
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {/* Vue + FastAPI Template */}
          <TkCard className="flex flex-col h-full">
            <TkCardHeader>
              <TkCardTitle>Vue.js + FastAPI</TkCardTitle>
            </TkCardHeader>
            <TkCardContent className="flex-1 space-y-4">
              <p className="text-sm opacity-80">Full-stack web application with authentication and i18n</p>
              <div className="flex flex-wrap gap-2">
                <TkBadge variant="default" size="sm">Vue.js 3</TkBadge>
                <TkBadge variant="default" size="sm">FastAPI</TkBadge>
                <TkBadge variant="warning" size="sm">Keycloak</TkBadge>
                <TkBadge variant="default" size="sm">PostgreSQL</TkBadge>
              </div>
            </TkCardContent>
            <TkCardFooter className="flex justify-end">
              <TkButton
                variant="default"
                size="sm"
                onClick={() => selectTemplate('https://github.com/thinkube/tkt-webapp-vue-fastapi')}
              >
                Deploy
              </TkButton>
            </TkCardFooter>
          </TkCard>

          {/* AI Model Inference Server */}
          <TkCard className="flex flex-col h-full">
            <TkCardHeader>
              <TkCardTitle>vLLM Server</TkCardTitle>
            </TkCardHeader>
            <TkCardContent className="flex-1 space-y-4">
              <p className="text-sm opacity-80">High-performance LLM inference (requires RTX 3090+)</p>
              <div className="flex flex-wrap gap-2">
                <TkBadge variant="default" size="sm">Gradio</TkBadge>
                <TkBadge variant="default" size="sm">FastAPI</TkBadge>
                <TkBadge variant="success" size="sm">GPU</TkBadge>
                <TkBadge variant="default" size="sm">HuggingFace</TkBadge>
              </div>
            </TkCardContent>
            <TkCardFooter className="flex justify-end">
              <TkButton
                variant="default"
                size="sm"
                onClick={() => selectTemplate('https://github.com/thinkube/tkt-vllm-gradio')}
              >
                Deploy
              </TkButton>
            </TkCardFooter>
          </TkCard>

          {/* Stable Diffusion Template */}
          <TkCard className="flex flex-col h-full">
            <TkCardHeader>
              <TkCardTitle>Stable Diffusion</TkCardTitle>
            </TkCardHeader>
            <TkCardContent className="flex-1 space-y-4">
              <p className="text-sm opacity-80">AI image generation with SDXL and SD 1.5 models</p>
              <div className="flex flex-wrap gap-2">
                <TkBadge variant="default" size="sm">Diffusers</TkBadge>
                <TkBadge variant="default" size="sm">Gradio</TkBadge>
                <TkBadge variant="success" size="sm">GPU</TkBadge>
                <TkBadge variant="default" size="sm">HuggingFace</TkBadge>
              </div>
            </TkCardContent>
            <TkCardFooter className="flex justify-end">
              <TkButton
                variant="default"
                size="sm"
                onClick={() => selectTemplate('https://github.com/thinkube/tkt-stable-diffusion')}
              >
                Deploy
              </TkButton>
            </TkCardFooter>
          </TkCard>
        </div>
      </div>
    </TkPageWrapper>
  )
}
