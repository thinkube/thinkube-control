import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Loader2, Upload, ExternalLink, Check } from 'lucide-react'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent, TkCardFooter } from 'thinkube-style/components/cards-data'
import { TkInput } from 'thinkube-style/components/forms-inputs'
import { TkInfoAlert, TkErrorAlert, TkSuccessAlert } from 'thinkube-style/components/feedback'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TkDialogRoot, TkDialogContent, TkDialogHeader, TkDialogTitle, TkDialogFooter } from 'thinkube-style/components/modals-overlays'
import { TemplateParameterForm } from '../components/TemplateParameterForm'
import { PlaybookExecutor, type PlaybookExecutorHandle } from '../components/PlaybookExecutor'
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
    type: 'str' | 'int' | 'bool' | 'choice'
    default?: string | number | boolean
    description?: string
    required?: boolean
    placeholder?: string
    pattern?: string
    minLength?: number
    maxLength?: number
    min?: number
    max?: number
    choices?: string[]
    group?: string
    order?: number
  }>
}

type DeployConfig = Record<string, string | number | boolean>

interface AvailableTemplate {
  name: string
  description: string
  url: string
  org: string
  deployment_type: 'app' | 'knative' | 'component'
  fixed_name?: string
  source: 'platform' | 'user'
}

interface DeployedApp {
  name: string
  path: string
  has_thinkube_yaml: boolean
  has_manifest_yaml: boolean
  description?: string
  deployment_type?: string
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
  const [availableTemplates, setAvailableTemplates] = useState<AvailableTemplate[]>([])
  const [loadingTemplates, setLoadingTemplates] = useState(false)
  const [deployConfig, setDeployConfig] = useState<DeployConfig>({
    project_name: '',
    project_description: ''
  })

  // Publish as Template state
  const [deployedApps, setDeployedApps] = useState<DeployedApp[]>([])
  const [loadingApps, setLoadingApps] = useState(false)
  const [showPublishDialog, setShowPublishDialog] = useState(false)
  const [publishApp, setPublishApp] = useState<DeployedApp | null>(null)
  const [publishTemplateName, setPublishTemplateName] = useState('')
  const [publishDescription, setPublishDescription] = useState('')
  const [publishTags, setPublishTags] = useState('')
  const [publishPrivate, setPublishPrivate] = useState(true)
  const [isPublishing, setIsPublishing] = useState(false)
  const [publishResult, setPublishResult] = useState<{ status: string; repo_url?: string; message?: string } | null>(null)

  const playbookExecutorRef = useRef<PlaybookExecutorHandle>(null!)
  const isLoadingRef = useRef(false) // Prevent concurrent loads

  // Compute domain name
  const domainName = typeof window !== 'undefined'
    ? window.location.hostname.replace('control.', '')
    : 'thinkube.com'

  // Determine if current template is a component
  const selectedTemplate = availableTemplates.find(t => t.url === templateUrl)
  const isComponent = selectedTemplate?.deployment_type === 'component'

  // Check if config is valid
  const isValidConfig =
    templateMetadata &&
    deployConfig.project_name &&
    /^[a-z][a-z0-9-]*$/.test(String(deployConfig.project_name))

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

  // Fetch available templates from metadata
  useEffect(() => {
    const fetchTemplates = async () => {
      setLoadingTemplates(true)
      try {
        const token = localStorage.getItem('access_token')
        const response = await api.get('/templates/list', {
          headers: { Authorization: `Bearer ${token}` }
        })

        if (response.data && response.data.templates) {
          setAvailableTemplates(response.data.templates)
        }
      } catch (error) {
        console.error('Failed to fetch available templates:', error)
      } finally {
        setLoadingTemplates(false)
      }
    }

    fetchTemplates()
  }, [])

  // Fetch deployed apps
  useEffect(() => {
    const fetchDeployedApps = async () => {
      setLoadingApps(true)
      try {
        const token = localStorage.getItem('access_token')
        const response = await api.get('/templates/apps', {
          headers: { Authorization: `Bearer ${token}` }
        })
        if (response.data?.apps) {
          setDeployedApps(response.data.apps)
        }
      } catch (error) {
        console.error('Failed to fetch deployed apps:', error)
      } finally {
        setLoadingApps(false)
      }
    }
    fetchDeployedApps()
  }, [])

  // Open publish dialog for an app
  const openPublishDialog = (app: DeployedApp) => {
    setPublishApp(app)
    setPublishTemplateName(app.name)
    setPublishDescription(app.description || '')
    setPublishTags('')
    setPublishPrivate(true)
    setPublishResult(null)
    setShowPublishDialog(true)
  }

  // Handle publish
  const handlePublish = async () => {
    if (!publishApp || !publishTemplateName) return
    setIsPublishing(true)
    setPublishResult(null)

    try {
      const token = localStorage.getItem('access_token')
      const response = await api.post('/templates/publish', {
        app_name: publishApp.name,
        template_name: publishTemplateName,
        description: publishDescription,
        tags: publishTags.split(',').map(t => t.trim()).filter(Boolean),
        private: publishPrivate,
      }, {
        headers: { Authorization: `Bearer ${token}` }
      })

      setPublishResult({
        status: 'success',
        repo_url: response.data.repo_url,
        message: response.data.message,
      })

      // Refresh template list since a new one was published
      const templatesResponse = await api.get('/templates/list', {
        headers: { Authorization: `Bearer ${token}` }
      })
      if (templatesResponse.data?.templates) {
        setAvailableTemplates(templatesResponse.data.templates)
      }
    } catch (error: any) {
      setPublishResult({
        status: 'error',
        message: error.response?.data?.detail || error.message,
      })
    } finally {
      setIsPublishing(false)
    }
  }

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
    const template = availableTemplates.find(t => t.url === url)
    if (template?.deployment_type === 'component' && template.fixed_name) {
      setDeployConfig(prev => ({ ...prev, project_name: template.fixed_name! }))
    }
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
    <TkPageWrapper description="Deploy pre-configured application templates to your Thinkube cluster">

      {/* Template Deployment Form */}
      {showDeployForm && (
        <TkCard className="mb-8">
          <TkCardHeader>
            <TkCardTitle>{isComponent ? 'Deploy Component' : 'Deploy Template'}</TkCardTitle>
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
                readOnlyName={isComponent}
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
              intent="ghost"
              onClick={cancelDeploy}
            >
              Cancel
            </TkButton>
            <TkButton
              disabled={!isValidConfig || isDeploying}
              onClick={handleDeployTemplate}
            >
              {isDeploying && (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              )}
              {isDeploying ? 'Deploying...' : isComponent ? 'Deploy Component' : 'Deploy Template'}
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
              disabled={!isValidUrl(manualTemplateUrl)}
              onClick={() => loadTemplate(manualTemplateUrl)}
            >
              Load Template
            </TkButton>
          </TkCardFooter>
        </TkCard>
      )}

      {/* Your Deployed Apps */}
      {deployedApps.length > 0 && (
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4">
            Your Apps
          </h2>
          <p className="text-sm opacity-70 mb-4">
            Publish a deployed app as a reusable template to your GitHub organization.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {deployedApps.map((app) => (
              <TkCard key={app.name} className="flex flex-col h-full">
                <TkCardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <TkCardTitle>{app.name}</TkCardTitle>
                    <TkBadge appearance={app.deployment_type === 'knative' ? 'muted' : app.deployment_type === 'component' ? 'prominent' : 'outlined'}>
                      {app.deployment_type === 'knative' ? 'Knative' : app.deployment_type === 'component' ? 'Component' : 'App'}
                    </TkBadge>
                  </div>
                </TkCardHeader>
                <TkCardContent className="flex-1">
                  <p className="text-sm opacity-80">{app.description || 'No description'}</p>
                </TkCardContent>
                <TkCardFooter className="flex justify-end">
                  <TkButton
                    intent="secondary"
                    size="sm"
                    onClick={() => openPublishDialog(app)}
                  >
                    <Upload className="h-4 w-4 mr-1" />
                    Publish as Template
                  </TkButton>
                </TkCardFooter>
              </TkCard>
            ))}
          </div>
        </div>
      )}

      {/* Publish Dialog */}
      <TkDialogRoot open={showPublishDialog} onOpenChange={setShowPublishDialog}>
        <TkDialogContent className="max-w-lg">
          <TkDialogHeader>
            <TkDialogTitle>Publish as Template</TkDialogTitle>
          </TkDialogHeader>

          <div className="space-y-4 py-4">
            {publishResult?.status === 'success' ? (
              <TkSuccessAlert title="Published">
                <p>{publishResult.message}</p>
                {publishResult.repo_url && (
                  <a
                    href={publishResult.repo_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-primary hover:underline mt-2"
                  >
                    View on GitHub <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </TkSuccessAlert>
            ) : publishResult?.status === 'error' ? (
              <TkErrorAlert title="Publish Failed">
                <p>{publishResult.message}</p>
              </TkErrorAlert>
            ) : null}

            <div className="space-y-2">
              <label className="text-sm font-medium">Source App</label>
              <TkInput value={publishApp?.name || ''} disabled />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">Template Name</label>
              <TkInput
                value={publishTemplateName}
                onChange={(e) => setPublishTemplateName(e.target.value)}
                placeholder="my-template"
                disabled={isPublishing || publishResult?.status === 'success'}
              />
              <p className="text-xs opacity-60">
                GitHub repository name. Use lowercase with hyphens.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">Description</label>
              <TkInput
                value={publishDescription}
                onChange={(e) => setPublishDescription(e.target.value)}
                placeholder="A brief description of what this template does"
                disabled={isPublishing || publishResult?.status === 'success'}
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">Tags</label>
              <TkInput
                value={publishTags}
                onChange={(e) => setPublishTags(e.target.value)}
                placeholder="webapp, ai, custom (comma-separated)"
                disabled={isPublishing || publishResult?.status === 'success'}
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="publish-private"
                checked={publishPrivate}
                onChange={(e) => setPublishPrivate(e.target.checked)}
                disabled={isPublishing || publishResult?.status === 'success'}
                className="rounded"
              />
              <label htmlFor="publish-private" className="text-sm">
                Private repository
              </label>
            </div>
          </div>

          <TkDialogFooter>
            <TkButton
              intent="ghost"
              onClick={() => setShowPublishDialog(false)}
            >
              {publishResult?.status === 'success' ? 'Close' : 'Cancel'}
            </TkButton>
            {publishResult?.status !== 'success' && (
              <TkButton
                disabled={!publishTemplateName || isPublishing}
                onClick={handlePublish}
              >
                {isPublishing && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                {isPublishing ? 'Publishing...' : 'Publish'}
              </TkButton>
            )}
          </TkDialogFooter>
        </TkDialogContent>
      </TkDialogRoot>

      {/* Available Templates */}
      <div className="mb-4">
        <h2 className="text-2xl font-bold mb-4">
          Available Templates
        </h2>

        {loadingTemplates && (
          <div className="flex items-center justify-center py-12">
            <div className="text-center">
              <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto mb-4" />
              <p>Loading templates...</p>
            </div>
          </div>
        )}

        {!loadingTemplates && availableTemplates.length === 0 && (
          <TkInfoAlert title="No Templates Available">
            <p>No application templates found in the metadata repository.</p>
          </TkInfoAlert>
        )}

        {!loadingTemplates && availableTemplates.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {availableTemplates.map((template) => (
              <TkCard key={template.name} className="flex flex-col h-full">
                <TkCardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <TkCardTitle>{template.name.replace('tkt-', '').replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}</TkCardTitle>
                    <div className="flex gap-1 shrink-0">
                      <TkBadge appearance={template.deployment_type === 'knative' ? 'muted' : template.deployment_type === 'component' ? 'prominent' : 'outlined'}>
                        {template.deployment_type === 'knative' ? 'Knative' : template.deployment_type === 'component' ? 'Component' : 'App'}
                      </TkBadge>
                      {template.source === 'user' && (
                        <TkBadge category="user">User</TkBadge>
                      )}
                    </div>
                  </div>
                </TkCardHeader>
                <TkCardContent className="flex-1">
                  <p className="text-sm opacity-80">{template.description}</p>
                  <p className="text-xs opacity-50 mt-2">{template.org}</p>
                </TkCardContent>
                <TkCardFooter className="flex justify-end">
                  <TkButton
                    size="sm"
                    onClick={() => selectTemplate(template.url)}
                  >
                    Deploy
                  </TkButton>
                </TkCardFooter>
              </TkCard>
            ))}
          </div>
        )}
      </div>
    </TkPageWrapper>
  )
}
