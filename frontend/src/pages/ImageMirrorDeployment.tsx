'use client'

import { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { TkButton } from 'thinkube-style/components/buttons-badges'
import { TkBadge } from 'thinkube-style/components/buttons-badges'
import {
  TkCard,
  TkCardHeader,
  TkCardTitle,
  TkCardContent,
  TkCardFooter,
} from 'thinkube-style/components/cards-data'
import { TkPageWrapper } from 'thinkube-style/components/utilities'
import { TkSuccessAlert, TkErrorAlert } from 'thinkube-style/components/feedback'
import { PlaybookExecutor } from '../components/PlaybookExecutor'
import api from '../lib/axios'

interface DeploymentVariables {
  source_image: string
  image_description?: string
}

interface Deployment {
  id: string
  status: 'pending' | 'running' | 'success' | 'failed'
  variables?: DeploymentVariables
}

interface ExecutionResult {
  status: 'success' | 'error' | 'failed' | 'cancelled'
  message?: string
  duration?: number
}

export function ImageMirrorDeployment() {
  const navigate = useNavigate()
  const { deploymentId } = useParams<{ deploymentId: string }>()

  const [deployment, setDeployment] = useState<Deployment | null>(null)
  const [deploymentName, setDeploymentName] = useState('Image Mirror')
  const [deploymentComplete, setDeploymentComplete] = useState(false)
  const [deploymentSuccess, setDeploymentSuccess] = useState(false)
  const [deploymentMessage, setDeploymentMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const playbookExecutorRef = useRef<{ startExecution: (wsPath: string) => void } | null>(null)

  // Badge variant mapping
  const getStatusBadgeVariant = (status: string) => {
    switch (status) {
      case 'success':
        return 'success'
      case 'failed':
        return 'destructive'
      case 'running':
        return 'warning'
      case 'pending':
        return 'default'
      default:
        return 'outline'
    }
  }

  useEffect(() => {
    if (!deploymentId) {
      navigate('/harbor-images')
      return
    }

    loadDeployment()
  }, [deploymentId, navigate])

  const loadDeployment = async () => {
    if (!deploymentId) return

    try {
      setLoading(true)
      const response = await api.get(`/templates/deployments/${deploymentId}`)
      const deploymentData: Deployment = response.data
      setDeployment(deploymentData)

      // Set deployment name based on source image
      if (deploymentData.variables?.source_image) {
        const source = deploymentData.variables.source_image
        const imageName = source.split('/').pop()?.split(':')[0] || 'Image'
        setDeploymentName(`Mirror: ${imageName}`)
      }

      // Start WebSocket connection if deployment is pending or running
      if (deploymentData.status === 'pending' || deploymentData.status === 'running') {
        const wsUrl = `/api/v1/ws/harbor/mirror/${deploymentId}`
        // Note: In the actual implementation, we'd need to expose startExecution
        // For now, this demonstrates the pattern
        setTimeout(() => {
          // Simulating the execution start
        }, 100)
      } else {
        // Deployment already completed
        setDeploymentComplete(true)
        setDeploymentSuccess(deploymentData.status === 'success')
        setDeploymentMessage(
          deploymentData.status === 'success'
            ? 'Image mirrored successfully!'
            : 'Image mirroring failed'
        )
      }
    } catch (err) {
      setError('Failed to load deployment details')
      setTimeout(() => navigate('/harbor-images'), 2000)
    } finally {
      setLoading(false)
    }
  }

  const loadDeploymentStatus = async () => {
    if (!deploymentId) return

    try {
      const response = await api.get(`/templates/deployments/${deploymentId}`)
      setDeployment(response.data)
    } catch (err) {
      // Error is silent, status reload is not critical
    }
  }

  const onDeploymentComplete = (result: ExecutionResult) => {
    setDeploymentComplete(true)
    setDeploymentSuccess(result.status === 'success')
    setDeploymentMessage(
      result.status === 'success'
        ? 'Image mirrored successfully!'
        : 'Image mirroring failed'
    )

    // Reload deployment status
    loadDeploymentStatus()
  }

  const goToImages = () => {
    navigate('/harbor-images')
  }

  if (loading) {
    return (
      <TkPageWrapper title="Image Mirror Deployment">
        <TkCard>
          <TkCardContent className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </TkCardContent>
        </TkCard>
      </TkPageWrapper>
    )
  }

  if (error) {
    return (
      <TkPageWrapper title="Image Mirror Deployment">
        <TkErrorAlert>{error}</TkErrorAlert>
      </TkPageWrapper>
    )
  }

  return (
    <TkPageWrapper title="Image Mirror Deployment">
      <TkCard>
        <TkCardHeader>
          <TkCardTitle>{deploymentName}</TkCardTitle>
        </TkCardHeader>

        <TkCardContent className="space-y-4">
          {deployment && (
            <TkCard variant="outline">
              <TkCardContent className="py-3">
                <span className="text-sm font-medium">Status:</span>
                {' '}
                <TkBadge variant={getStatusBadgeVariant(deployment.status)}>
                  {deployment.status}
                </TkBadge>
                {deployment.variables && (
                  <>
                    <div className="mt-2 text-sm">Source: {deployment.variables.source_image}</div>
                    <div className="text-sm">
                      Description: {deployment.variables.image_description || '-'}
                    </div>
                  </>
                )}
              </TkCardContent>
            </TkCard>
          )}

          {/* Playbook Executor for WebSocket streaming */}
          <PlaybookExecutor
            title={`Mirroring: ${deploymentName}`}
            websocketPath={`/api/v1/ws/harbor/mirror/${deploymentId}`}
            onComplete={onDeploymentComplete}
          />

          {deploymentComplete && (
            <>
              {deploymentSuccess ? (
                <TkSuccessAlert>{deploymentMessage}</TkSuccessAlert>
              ) : (
                <TkErrorAlert>{deploymentMessage}</TkErrorAlert>
              )}
            </>
          )}
        </TkCardContent>

        {deploymentComplete && (
          <TkCardFooter className="justify-end">
            <TkButton onClick={goToImages}>Back to Images</TkButton>
          </TkCardFooter>
        )}
      </TkCard>
    </TkPageWrapper>
  )
}
