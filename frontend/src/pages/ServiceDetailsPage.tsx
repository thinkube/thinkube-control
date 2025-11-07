import { useEffect, useState, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
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
  Loader2,
  ChevronRight
} from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkButton, TkBadge, TkGpuBadge } from 'thinkube-style/components/buttons-badges';
import { TkSwitch } from 'thinkube-style/components/forms-inputs';
import { TkTooltip, TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays';
import { TkBrandIcon } from 'thinkube-style/components/brand-icons';
import { TkSeparator, TkPageWrapper } from 'thinkube-style/components/utilities';
import { TkCodeBlock } from 'thinkube-style/components/feedback';
import { TkTabs, TkTabsList, TkTabsTrigger } from 'thinkube-style/components/navigation';
import { toast } from 'sonner';
import { HealthHistoryChart } from '@/components/HealthHistoryChart';

// Type interfaces
interface HealthData {
  uptime_percentage: number;
  actual_checks: number;
  monitoring_coverage: number;
  health_history: any[];
}

interface Endpoint {
  id: string;
  name: string;
  type: string;
  url?: string;
  is_primary: boolean;
  is_internal: boolean;
  health_status?: 'healthy' | 'unhealthy' | 'unknown';
  description?: string;
}

interface Dependency {
  name: string;
  enabled: boolean;
  health_status?: 'healthy' | 'unhealthy' | 'unknown' | 'disabled';
  service_id?: string;
}

interface ResourceUsage {
  cpu_requests_millicores: number;
  memory_requests_human: string;
}

interface PodInfo {
  name: string;
  status: string;
  ready: boolean;
  node: string;
  restart_count: number;
  containers?: Container[];
}

interface Container {
  name: string;
  image: string;
  state: 'running' | 'waiting' | 'terminated';
  restart_count: number;
  resources?: {
    cpu_request?: string;
    memory_request?: string;
    gpu_request?: string;
  };
}

interface ServiceDetails {
  dependencies?: Dependency[];
  resource_usage?: ResourceUsage;
  pods_info?: PodInfo[];
  recent_actions?: RecentAction[];
}

interface RecentAction {
  id: string;
  action: string;
  performed_by: string;
  performed_at: string;
}

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
    getContainerLogs,
    describePod,
  } = useServicesStore();

  const [loading, setLoading] = useState(true);
  const [serviceDetails, setServiceDetails] = useState<ServiceDetails | null>(null);
  const [healthData, setHealthData] = useState<HealthData | null>(null);
  const [toggling, setToggling] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [showRestartConfirm, setShowRestartConfirm] = useState(false);
  const [expandedPods, setExpandedPods] = useState<Record<string, boolean>>({});
  const [selectedContainer, setSelectedContainer] = useState<{ podName: string; containerName: string } | null>(null);
  const [containerLogs, setContainerLogs] = useState<string>('');
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logLines, setLogLines] = useState(100);
  const [selectedPod, setSelectedPod] = useState<string | null>(null);
  const [podDescription, setPodDescription] = useState<string>('');
  const [loadingDescription, setLoadingDescription] = useState(false);
  const [healthTimeRange, setHealthTimeRange] = useState<'hour' | 'day' | 'week'>('day');

  // Get basic service info from store
  const service = services.find(s => s.id === id);

  // Fetch full service details
  useEffect(() => {
    if (!id) return;

    const loadDetails = async () => {
      setLoading(true);
      try {
        const [details, health] = await Promise.all([
          fetchServiceDetails(id),
          useServicesStore.getState().fetchHealthHistory?.(id) || Promise.resolve(null),
        ]);
        setServiceDetails(details);
        setHealthData(health);
      } catch (error) {
        toast.error('Failed to load service details');
      } finally {
        setLoading(false);
      }
    };

    loadDetails();
  }, [id, fetchServiceDetails]);

  // Filter health history based on selected time range - MUST be called on every render (before early returns)
  const filteredHealthHistory = useMemo(() => {
    if (!healthData?.health_history || !Array.isArray(healthData.health_history)) return [];

    const now = new Date();
    const timeRanges = {
      hour: 60 * 60 * 1000,      // 1 hour in milliseconds
      day: 24 * 60 * 60 * 1000,  // 24 hours
      week: 7 * 24 * 60 * 60 * 1000  // 7 days
    };

    const cutoffTime = new Date(now.getTime() - timeRanges[healthTimeRange]);

    return healthData.health_history.filter(item => {
      const itemTime = new Date(item.checked_at);
      return itemTime >= cutoffTime;
    });
  }, [healthData?.health_history, healthTimeRange]);

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
      const result = await triggerHealthCheck(id);

      // Reload details
      const details = await fetchServiceDetails(id);
      setServiceDetails(details);

      // Show result based on health status
      if (result.status === 'disabled') {
        toast.info('Service is currently disabled');
      } else if (result.status === 'healthy') {
        const responseTime = result.response_time ? ` (${result.response_time}ms)` : '';
        toast.success(`Service is healthy${responseTime}`);
      } else if (result.status === 'unhealthy') {
        const errorMsg = result.error_message ? `: ${result.error_message}` : '';
        toast.error(`Service is unhealthy${errorMsg}`);
      } else {
        toast.warning('Health status is unknown');
      }
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

  const togglePod = (podName: string) => {
    setExpandedPods(prev => ({ ...prev, [podName]: !prev[podName] }));
  };

  const handleViewContainerLogs = async (podName: string, containerName: string) => {
    if (!id) return;

    setSelectedContainer({ podName, containerName });
    setLoadingLogs(true);

    try {
      const response = await getContainerLogs(id, podName, containerName, logLines);
      const logData = typeof response === 'string' ? response : response.logs;
      setContainerLogs(logData);
    } catch (error) {
      toast.error(`Failed to load logs for ${containerName}`);
      setContainerLogs('Error: Failed to get container logs');
    } finally {
      setLoadingLogs(false);
    }
  };

  const handleDescribePod = async (podName: string) => {
    if (!id) return;

    setSelectedPod(podName);
    setLoadingDescription(true);

    try {
      const response = await describePod(id, podName);
      const description = response.formatted || JSON.stringify(response, null, 2);
      setPodDescription(description);
    } catch (error) {
      toast.error(`Failed to load description for ${podName}`);
      setPodDescription('Error: Failed to get pod description');
    } finally {
      setLoadingDescription(false);
    }
  };

  const formatServiceType = (type: string) => {
    const typeLabels: Record<string, string> = {
      'core': 'Core',
      'optional': 'Optional',
      'user_app': 'User App'
    };
    return typeLabels[type] || type;
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  if (loading || !service) {
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

  // Extract data from service details
  const pods = serviceDetails?.pods_info || [];
  const endpoints = (service as any).endpoints || [];
  const dependencies = serviceDetails?.dependencies || [];
  const resourceUsage = serviceDetails?.resource_usage;
  const recentActions = serviceDetails?.recent_actions || [];

  // Debug logging for dependencies
  console.log('Service Details - Dependencies:', dependencies);
  console.log('Service Details - Full serviceDetails:', serviceDetails);

  return (
    <TkPageWrapper>
      {/* Back button */}
      <div className="mb-6">
        <TkButton
          variant="ghost"
          size="sm"
          onClick={() => navigate('/dashboard/favorites')}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Dashboard
        </TkButton>
      </div>

      <div className="space-y-6">

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
                  {formatServiceType(service.type)}
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
        </TkCardContent>
      </TkCard>

      {/* Basic Info and Health */}
      <div className="grid grid-cols-2 gap-6">
        {/* Basic Info */}
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Basic Information</TkCardTitle>
          </TkCardHeader>
          <TkCardContent className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Namespace:</span>
              <span className="font-medium">{service.namespace}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Category:</span>
              <span className="font-medium">{service.category || '-'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Status:</span>
              <TkBadge variant={service.is_enabled ? 'success' : 'warning'}>
                {service.is_enabled ? 'Enabled' : 'Disabled'}
              </TkBadge>
            </div>
            {service.url && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">URL:</span>
                <a
                  href={service.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline text-sm"
                >
                  {service.url}
                </a>
              </div>
            )}
          </TkCardContent>
        </TkCard>

        {/* Health */}
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Health Status</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            {healthData ? (
              <div className="space-y-3">
                <div>
                  <div className="text-sm text-muted-foreground mb-1">Uptime</div>
                  <div className="text-3xl font-bold">{healthData.uptime_percentage}%</div>
                </div>
                <div className="text-sm space-y-1">
                  <div className="text-muted-foreground">
                    {healthData.actual_checks} checks performed
                  </div>
                  {healthData.monitoring_coverage < 100 && (
                    <div className="text-warning">
                      Coverage: {healthData.monitoring_coverage}%
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No health data available</p>
            )}
          </TkCardContent>
        </TkCard>
      </div>

      {/* Health History Chart */}
      {healthData && healthData.health_history && healthData.health_history.length > 0 && filteredHealthHistory && filteredHealthHistory.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <div className="flex items-center justify-between">
              <TkCardTitle>Health History</TkCardTitle>
              <TkTabs value={healthTimeRange} onValueChange={(value) => setHealthTimeRange(value as 'hour' | 'day' | 'week')}>
                <TkTabsList>
                  <TkTabsTrigger value="hour">Last Hour</TkTabsTrigger>
                  <TkTabsTrigger value="day">Last Day</TkTabsTrigger>
                  <TkTabsTrigger value="week">Last Week</TkTabsTrigger>
                </TkTabsList>
              </TkTabs>
            </div>
          </TkCardHeader>
          <TkCardContent>
            <HealthHistoryChart data={filteredHealthHistory} />
          </TkCardContent>
        </TkCard>
      )}

      {/* Endpoints */}
      {endpoints.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Endpoints</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <div className="space-y-3">
              {endpoints.map((endpoint: Endpoint, index: number) => (
                <div key={endpoint.id}>
                  {index > 0 && <TkSeparator />}
                  <TkCard variant={endpoint.is_primary ? 'default' : 'outline'}>
                    <TkCardContent standalone>
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{endpoint.name}</span>
                          {endpoint.is_primary && (
                            <TkBadge variant="default" className="text-xs">Primary</TkBadge>
                          )}
                          <TkBadge variant="secondary" className="text-xs">{endpoint.type}</TkBadge>
                        </div>
                        {endpoint.health_status && (
                          <TkBadge
                            variant={
                              endpoint.health_status === 'healthy' ? 'success' :
                              endpoint.health_status === 'unhealthy' ? 'destructive' :
                              'warning'
                            }
                            className="text-xs"
                          >
                            {endpoint.health_status}
                          </TkBadge>
                        )}
                      </div>
                      {endpoint.description && (
                        <p className="text-sm text-muted-foreground mb-2">{endpoint.description}</p>
                      )}
                      {endpoint.url && !endpoint.is_internal ? (
                        <a
                          href={endpoint.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-primary hover:underline"
                        >
                          {endpoint.url}
                        </a>
                      ) : endpoint.is_internal ? (
                        <span className="text-sm text-muted-foreground">Internal endpoint</span>
                      ) : null}
                    </TkCardContent>
                  </TkCard>
                </div>
              ))}
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {/* Dependencies */}
      {dependencies.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Dependencies</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <div className="flex flex-wrap gap-2">
              {dependencies.map((dep) => {
                // Determine badge variant based on health status and enabled state
                let variant: 'default' | 'success' | 'destructive' | 'secondary' = 'default';
                if (!dep.enabled) {
                  variant = 'destructive';
                } else if (dep.health_status === 'healthy') {
                  variant = 'success';
                } else if (dep.health_status === 'unhealthy' || dep.health_status === 'disabled') {
                  variant = 'destructive';
                } else if (dep.health_status === 'unknown') {
                  variant = 'secondary';
                }

                const label = `${dep.name}${!dep.enabled ? ' (disabled)' : ''}${dep.enabled && dep.health_status ? ` (${dep.health_status})` : ''}`;

                // If service_id is available, make it clickable with TkButton
                return dep.service_id ? (
                  <TkButton
                    key={dep.name}
                    variant={variant}
                    size="sm"
                    onClick={() => navigate(`/services/${dep.service_id}`)}
                  >
                    {label}
                  </TkButton>
                ) : (
                  <TkBadge
                    key={dep.name}
                    variant={variant}
                  >
                    {label}
                  </TkBadge>
                );
              })}
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {/* Resource Usage */}
      {resourceUsage && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Resource Usage</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">CPU Requests:</span>
                <span className="ml-2 font-medium">{resourceUsage.cpu_requests_millicores}m</span>
              </div>
              <div>
                <span className="text-muted-foreground">Memory Requests:</span>
                <span className="ml-2 font-medium">{resourceUsage.memory_requests_human}</span>
              </div>
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {/* Pods List */}
      {pods.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Pods ({pods.length})</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <div className="space-y-3">
              {pods.map((pod, index) => (
                <div key={pod.name}>
                  {index > 0 && <TkSeparator />}
                  <TkCard>
                    <TkCardContent standalone>
                      {/* Pod Header */}
                      <div
                        className="cursor-pointer"
                        onClick={() => togglePod(pod.name)}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <ChevronRight
                              className={`w-4 h-4 transition-transform ${expandedPods[pod.name] ? 'rotate-90' : ''}`}
                            />
                            <span className="font-medium">{pod.name}</span>
                            <TkBadge variant={pod.ready ? 'success' : 'warning'} className="text-xs">
                              {pod.status}
                            </TkBadge>
                          </div>
                          <div className="flex items-center gap-4 text-sm text-muted-foreground">
                            <span>Node: {pod.node}</span>
                            <span>Restarts: {pod.restart_count}</span>
                            <TkButton
                              size="sm"
                              variant="outline"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDescribePod(pod.name);
                              }}
                            >
                              Describe Pod
                            </TkButton>
                          </div>
                        </div>
                      </div>

                      {/* Pod Details (expandable) */}
                      {expandedPods[pod.name] && pod.containers && pod.containers.length > 0 && (
                        <div className="mt-3 space-y-2">
                          <TkSeparator />
                          <h5 className="font-medium">Containers ({pod.containers.length})</h5>
                          {pod.containers.map((container) => (
                            <TkCard key={container.name} variant="outline">
                              <TkCardContent standalone>
                                <div className="flex items-center justify-between mb-2">
                                  <div>
                                    <span className="font-medium">{container.name}</span>
                                    {container.state && (
                                      <TkBadge
                                        variant={
                                          container.state === 'running' ? 'success' :
                                          container.state === 'waiting' ? 'warning' :
                                          'destructive'
                                        }
                                        className="ml-2 text-xs"
                                      >
                                        {container.state.charAt(0).toUpperCase() + container.state.slice(1)}
                                      </TkBadge>
                                    )}
                                  </div>
                                  <TkButton
                                    size="sm"
                                    variant="outline"
                                    onClick={() => handleViewContainerLogs(pod.name, container.name)}
                                  >
                                    View Logs
                                  </TkButton>
                                </div>
                                <div className="text-xs text-muted-foreground space-y-1">
                                  <div>Image: {container.image}</div>
                                  {container.resources && (
                                    <div className="flex gap-3">
                                      {container.resources.cpu_request && (
                                        <span>CPU: {container.resources.cpu_request}</span>
                                      )}
                                      {container.resources.memory_request && (
                                        <span>Memory: {container.resources.memory_request}</span>
                                      )}
                                      {container.resources.gpu_request && container.resources.gpu_request !== '0' && (
                                        <span>GPU: {container.resources.gpu_request}</span>
                                      )}
                                    </div>
                                  )}
                                  {container.restart_count > 0 && (
                                    <div className="text-warning">Restarts: {container.restart_count}</div>
                                  )}
                                </div>
                              </TkCardContent>
                            </TkCard>
                          ))}
                        </div>
                      )}
                    </TkCardContent>
                  </TkCard>
                </div>
              ))}
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {/* Recent Actions */}
      {recentActions.length > 0 && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Recent Actions</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <div className="space-y-2">
              {recentActions.map((action) => (
                <TkCard key={action.id} variant="outline">
                  <TkCardContent standalone>
                    <div className="flex items-center justify-between text-sm">
                      <div className="flex items-center gap-2">
                        <TkBadge variant="outline" className="text-xs">{action.action}</TkBadge>
                        <span>{action.action}</span>
                      </div>
                      <div className="text-muted-foreground">
                        <span>{action.performed_by}</span>
                        <span className="ml-2">{formatDate(action.performed_at)}</span>
                      </div>
                    </div>
                  </TkCardContent>
                </TkCard>
              ))}
            </div>
          </TkCardContent>
        </TkCard>
      )}

      {/* Pod Description Viewer */}
      {selectedPod && (
        <TkCard>
          <TkCardHeader>
            <div className="flex items-center justify-between">
              <TkCardTitle>
                Pod Description: {selectedPod}
              </TkCardTitle>
              <TkButton
                size="sm"
                variant="ghost"
                onClick={() => setSelectedPod(null)}
              >
                Close
              </TkButton>
            </div>
          </TkCardHeader>
          <TkCardContent>
            {loadingDescription ? (
              <div className="flex items-center justify-center h-64">
                <Loader2 className="animate-spin h-6 w-6" />
              </div>
            ) : (
              <TkCodeBlock>
                {podDescription || 'No description available'}
              </TkCodeBlock>
            )}
          </TkCardContent>
        </TkCard>
      )}

      {/* Container Logs Viewer */}
      {selectedContainer && (
        <TkCard>
          <TkCardHeader>
            <div className="flex items-center justify-between">
              <TkCardTitle>
                Container Logs: {selectedContainer.containerName}
              </TkCardTitle>
              <TkButton
                size="sm"
                variant="ghost"
                onClick={() => setSelectedContainer(null)}
              >
                Close
              </TkButton>
            </div>
            <p className="text-sm text-muted-foreground mt-1">
              Pod: {selectedContainer.podName}
            </p>
          </TkCardHeader>
          <TkCardContent>
            {loadingLogs ? (
              <div className="flex items-center justify-center h-64">
                <Loader2 className="animate-spin h-6 w-6" />
              </div>
            ) : (
              <TkCodeBlock>
                {containerLogs || 'No logs available'}
              </TkCodeBlock>
            )}
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
    </TkPageWrapper>
  );
}
