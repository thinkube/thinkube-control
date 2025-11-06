import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useServicesStore } from '@/stores/useServicesStore';
import {
  ArrowLeft,
  ExternalLink,
  RotateCw,
  Heart,
  Star,
  Server,
  Code,
  BarChart3,
  Shield,
  Database,
  Cpu,
  FileText,
  Box,
  Loader2
} from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge, TkGpuBadge } from 'thinkube-style/components/buttons-badges';
import { TkSwitch } from 'thinkube-style/components/forms-inputs';
import { TkTooltip, TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays';
import { TkBrandIcon } from 'thinkube-style/components/brand-icons';
import { TkSeparator } from 'thinkube-style/components/utilities';
import { toast } from 'sonner';

export default function ServiceDetailsPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const {
    services,
    fetchServiceDetails,
    toggleService,
    restartService,
    triggerHealthCheck,
    toggleFavorite,
  } = useServicesStore();

  const [loading, setLoading] = useState(true);
  const [serviceDetails, setServiceDetails] = useState<any>(null);
  const [toggling, setToggling] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [showRestartConfirm, setShowRestartConfirm] = useState(false);

  // Get basic service info from store
  const service = services.find(s => s.id === id);

  // Fetch full service details
  useEffect(() => {
    if (!id) return;

    const loadDetails = async () => {
      setLoading(true);
      try {
        const details = await fetchServiceDetails(id);
        setServiceDetails(details);
      } catch (error) {
        console.error('Failed to load service details:', error);
        toast.error('Failed to load service details');
      } finally {
        setLoading(false);
      }
    };

    loadDetails();
  }, [id, fetchServiceDetails]);

  // Handlers
  const handleToggle = async (checked: boolean) => {
    if (!id) return;
    setToggling(true);
    try {
      await toggleService(id, checked);
      toast.success(`Service ${checked ? 'enabled' : 'disabled'}`);
      // Reload details
      const details = await fetchServiceDetails(id);
      setServiceDetails(details);
    } catch (error) {
      toast.error(`Failed to ${checked ? 'enable' : 'disable'} service`);
    } finally {
      setToggling(false);
    }
  };

  const handleRestart = async () => {
    if (!id) return;
    setRestarting(true);
    setShowRestartConfirm(false);
    try {
      await restartService(id);
      toast.success('Service restarted successfully');
      // Reload details after a delay
      setTimeout(async () => {
        const details = await fetchServiceDetails(id);
        setServiceDetails(details);
      }, 2000);
    } catch (error) {
      toast.error('Failed to restart service');
    } finally {
      setTimeout(() => setRestarting(false), 1000);
    }
  };

  const handleHealthCheck = async () => {
    if (!id) return;
    setCheckingHealth(true);
    try {
      await triggerHealthCheck(id);
      toast.success('Health check triggered');
      // Reload details
      const details = await fetchServiceDetails(id);
      setServiceDetails(details);
    } catch (error) {
      toast.error('Failed to trigger health check');
    } finally {
      setCheckingHealth(false);
    }
  };

  const handleToggleFavorite = async () => {
    if (!service) return;
    try {
      await toggleFavorite(service);
      toast.success(service.is_favorite ? 'Removed from favorites' : 'Added to favorites');
    } catch (error) {
      toast.error('Failed to toggle favorite');
    }
  };

  if (loading || !service || !serviceDetails) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Loader2 className="animate-spin h-8 w-8 mx-auto mb-4" />
          <p className="text-muted-foreground">Loading service details...</p>
        </div>
      </div>
    );
  }

  // Health status
  const healthStatus = !service.is_enabled
    ? 'disabled'
    : service.latest_health?.status || 'unknown';

  const statusBadgeVariant = {
    healthy: 'success' as const,
    unhealthy: 'destructive' as const,
    unknown: 'warning' as const,
    disabled: 'secondary' as const,
  }[healthStatus] || 'secondary' as const;

  const statusLabel = {
    healthy: 'Healthy',
    unhealthy: 'Unhealthy',
    unknown: 'Unknown',
    disabled: 'Disabled',
  }[healthStatus] || 'Unknown';

  // Type badge
  const typeVariant = {
    core: 'default' as const,
    optional: 'secondary' as const,
    user_app: 'outline' as const,
  }[service.type] || 'outline' as const;

  // Get icon
  const categoryIconMap: Record<string, any> = {
    infrastructure: Server,
    development: Code,
    monitoring: BarChart3,
    security: Shield,
    storage: Database,
    ai: Cpu,
    documentation: FileText,
    application: Box,
  };

  const getIconComponent = () => {
    if (service.icon && service.icon.startsWith('/')) {
      return null;
    }
    return categoryIconMap[service.category?.toLowerCase() || ''] || Server;
  };

  const IconComponent = getIconComponent();
  const hasCustomIcon = service.icon && service.icon.startsWith('/');

  // Check if URL is web accessible
  const isWebUrl = (url?: string) => {
    if (!url) return false;
    if (!url.startsWith('http://') && !url.startsWith('https://')) return false;
    if (url.includes('.svc.cluster.local') || url.includes('internal')) return false;
    return true;
  };

  // Extract pods from service details
  const pods = serviceDetails.pods || [];

  return (
    <div className="space-y-6">
      {/* Back button */}
      <div>
        <TkButton
          variant="ghost"
          size="sm"
          onClick={() => navigate('/dashboard/favorites')}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Dashboard
        </TkButton>
      </div>

      {/* Service Header */}
      <TkCard>
        <TkCardHeader className="pb-4">
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                {hasCustomIcon ? (
                  <TkBrandIcon
                    icon={service.icon!.replace('/icons/', '').replace('.svg', '')}
                    alt={service.display_name || service.name}
                    size={32}
                  />
                ) : IconComponent ? (
                  <IconComponent className="w-8 h-8" />
                ) : null}
                <div>
                  <TkCardTitle className="text-2xl">{service.display_name || service.name}</TkCardTitle>
                  {service.description && (
                    <p className="text-sm text-muted-foreground mt-1">{service.description}</p>
                  )}
                </div>
              </div>

              {/* Badges */}
              <div className="flex flex-wrap gap-2 mt-3">
                <TkBadge variant={statusBadgeVariant}>{statusLabel}</TkBadge>
                <TkBadge variant={typeVariant}>
                  {service.type === 'core' ? 'Core' : service.type === 'optional' ? 'Optional' : 'User App'}
                </TkBadge>
                {service.category && (
                  <TkBadge variant="outline">{service.category}</TkBadge>
                )}
                {service.gpu_count && service.gpu_count > 0 && (
                  <TkGpuBadge gpuCount={service.gpu_count} />
                )}
              </div>
            </div>

            {/* Favorite button */}
            <TkTooltip content={service.is_favorite ? 'Remove from favorites' : 'Add to favorites'}>
              <TkButton
                variant="ghost"
                size="icon"
                onClick={handleToggleFavorite}
              >
                <Star className={`h-5 w-5 ${service.is_favorite ? 'fill-warning text-warning' : ''}`} />
              </TkButton>
            </TkTooltip>
          </div>
        </TkCardHeader>

        <TkCardContent className="space-y-4">
          {/* Actions */}
          <div className="flex gap-2">
            {service.is_enabled && isWebUrl(service.url) && (
              <TkButton variant="default" asChild>
                <a href={service.url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="h-4 w-4 mr-2" />
                  Open Service
                </a>
              </TkButton>
            )}

            {service.is_enabled && (
              <TkTooltip content="Restart service">
                <TkButton
                  variant="outline"
                  onClick={() => setShowRestartConfirm(true)}
                  disabled={restarting}
                >
                  <RotateCw className={`h-4 w-4 mr-2 ${restarting ? 'animate-spin' : ''}`} />
                  Restart
                </TkButton>
              </TkTooltip>
            )}

            {service.is_enabled && (
              <TkTooltip content="Check health">
                <TkButton
                  variant="outline"
                  onClick={handleHealthCheck}
                  disabled={checkingHealth}
                >
                  <Heart className={`h-4 w-4 mr-2 ${checkingHealth ? 'animate-pulse text-destructive' : ''}`} />
                  Health Check
                </TkButton>
              </TkTooltip>
            )}
          </div>

          {/* Toggle */}
          {service.can_be_disabled && (
            <>
              <TkSeparator />
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">Service Enabled</span>
                <TkSwitch
                  checked={service.is_enabled}
                  onCheckedChange={handleToggle}
                  disabled={toggling}
                />
              </div>
            </>
          )}

          {/* Health Details */}
          {service.latest_health && (
            <>
              <TkSeparator />
              <div className="space-y-2 text-sm">
                <h4 className="font-medium">Health Status</h4>
                {service.latest_health.pod_status && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Pods:</span>
                    <span className={
                      service.latest_health.pod_status.includes('Running')
                        ? 'text-[var(--color-success)]'
                        : 'text-[var(--color-error)]'
                    }>
                      {service.latest_health.pod_status}
                    </span>
                  </div>
                )}
                {service.latest_health.checked_at && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Last Checked:</span>
                    <span>{new Date(service.latest_health.checked_at).toLocaleString()}</span>
                  </div>
                )}
              </div>
            </>
          )}
        </TkCardContent>
      </TkCard>

      {/* Pods List */}
      {pods.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Pods ({pods.length})</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <div className="space-y-3">
              {pods.map((pod: any, index: number) => (
                <div key={pod.name}>
                  {index > 0 && <TkSeparator />}
                  <div className="flex items-center justify-between">
                    <div className="flex-1">
                      <div className="font-medium">{pod.name}</div>
                      <div className="text-sm text-muted-foreground">
                        Status: <span className={pod.status === 'Running' ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}>{pod.status}</span>
                        {pod.restarts !== undefined && ` • Restarts: ${pod.restarts}`}
                      </div>
                    </div>
                    <Link to={`/services/${id}/pods/${pod.name}`}>
                      <TkButton variant="outline" size="sm">
                        View Details
                      </TkButton>
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {/* Restart Confirmation */}
      <TkControlledConfirmDialog
        open={showRestartConfirm}
        onOpenChange={setShowRestartConfirm}
        title="Restart Service"
        description={`Are you sure you want to restart ${service.display_name || service.name}? This will temporarily interrupt the service.`}
        confirmText="Restart"
        cancelText="Cancel"
        onConfirm={handleRestart}
        variant="destructive"
      />
    </div>
  );
}
