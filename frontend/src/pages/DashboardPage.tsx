import { useEffect, useState } from 'react';
import { useLocation, useParams, useNavigate } from 'react-router-dom';
import { useServicesStore } from '@/stores/useServicesStore';
import { AlertCircle, Loader2, RefreshCw, Star } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkSwitch } from 'thinkube-style/components/forms-inputs';
import { TkControlledConfirmDialog } from 'thinkube-style/components/modals-overlays';
import { ServiceCard } from '@/components/ServiceCard';
import type { Service } from '@/stores/useServicesStore';
import { toast } from 'sonner';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  rectSortingStrategy,
} from '@dnd-kit/sortable';
import { SortableServiceCard } from '@/components/SortableServiceCard';

export default function DashboardPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { category } = useParams<{ category?: string }>();
  const {
    services,
    loading,
    error,
    fetchServices,
    syncServices,
    getFavoriteServicesComputed,
    getFilteredServices,
    getCategories,
    setCategoryFilter,
    toggleFavorite,
    toggleService,
    restartService,
    triggerHealthCheck,
    reorderFavorites,
  } = useServicesStore();
  const [compactMode, setCompactMode] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [serviceToRestart, setServiceToRestart] = useState<Service | null>(null);

  // Drag-and-drop sensors
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Determine view based on route
  const isAllServicesView = location.pathname === '/dashboard/all';
  const isCategoryView = location.pathname.startsWith('/dashboard/category/');
  const isFavoritesView = !isAllServicesView && !isCategoryView;

  // Get filtered services
  const filteredServices = getFilteredServices();
  const favoriteServices = getFavoriteServicesComputed();

  // Set category filter based on route
  useEffect(() => {
    if (isCategoryView && category) {
      // Find the actual category value from backend (case-sensitive match)
      const categories = getCategories();
      const actualCategory = categories.find(
        cat => cat.toLowerCase() === category.toLowerCase()
      );
      setCategoryFilter(actualCategory || null);
    } else {
      setCategoryFilter(null);
    }
  }, [category, isCategoryView, setCategoryFilter, getCategories]);

  // Service card handlers
  const handleToggleFavorite = async (service: Service) => {
    try {
      await toggleFavorite(service);
      toast.success(service.favorite ? 'Removed from favorites' : 'Added to favorites');
    } catch (error) {
      toast.error('Failed to toggle favorite');
      console.error('Failed to toggle favorite:', error);
    }
  };

  const handleShowDetails = (service: Service) => {
    navigate(`/services/${service.id}`);
  };

  const handleRestart = (service: Service) => {
    setServiceToRestart(service);
  };

  const confirmRestart = async () => {
    if (!serviceToRestart) return;

    try {
      await restartService(serviceToRestart.id);
      toast.success(`${serviceToRestart.name} restarted successfully`);
    } catch (error) {
      toast.error(`Failed to restart ${serviceToRestart.name}`);
      console.error('Failed to restart service:', error);
    } finally {
      setServiceToRestart(null);
    }
  };

  const handleToggleService = async (service: Service, enabled: boolean) => {
    try {
      await toggleService(service.id, enabled);
      toast.success(`${service.name} ${enabled ? 'enabled' : 'disabled'}`);
    } catch (error) {
      toast.error(`Failed to ${enabled ? 'enable' : 'disable'} ${service.name}`);
      console.error('Failed to toggle service:', error);
    }
  };

  const handleHealthCheck = async (service: Service) => {
    try {
      const result = await triggerHealthCheck(service.id);

      // Show result based on health status
      if (result.status === 'disabled') {
        toast.info(`${service.name} is currently disabled`);
      } else if (result.status === 'healthy') {
        const responseTime = result.response_time ? ` (${result.response_time}ms)` : '';
        toast.success(`${service.name} is healthy${responseTime}`);
      } else if (result.status === 'unhealthy') {
        const errorMsg = result.error_message ? `: ${result.error_message}` : '';
        toast.error(`${service.name} is unhealthy${errorMsg}`);
      } else {
        toast.warning(`${service.name} health status is unknown`);
      }
    } catch (error) {
      toast.error(`Failed to check health for ${service.name}`);
      console.error('Failed to check health:', error);
    }
  };

  // Load compact mode from localStorage on mount
  useEffect(() => {
    const savedCompactMode = localStorage.getItem('dashboardCompactMode');
    if (savedCompactMode !== null) {
      setCompactMode(savedCompactMode === 'true');
    }
  }, []);

  // Save compact mode to localStorage when it changes
  const handleCompactModeChange = (checked: boolean) => {
    setCompactMode(checked);
    localStorage.setItem('dashboardCompactMode', checked.toString());
  };

  // Handle sync services
  const handleSync = async () => {
    setSyncing(true);
    try {
      await syncServices();
      toast.success('Services synced successfully');
    } catch (error) {
      toast.error('Failed to sync services');
      console.error('Failed to sync services:', error);
    } finally {
      setSyncing(false);
    }
  };

  // Handle drag end for favorites reordering
  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;

    if (over && active.id !== over.id) {
      const oldIndex = favoriteServices.findIndex((s) => s.id === active.id);
      const newIndex = favoriteServices.findIndex((s) => s.id === over.id);

      if (oldIndex !== -1 && newIndex !== -1) {
        const newOrder = arrayMove(favoriteServices, oldIndex, newIndex);
        const serviceIds = newOrder.map((s) => s.id);

        try {
          await reorderFavorites(serviceIds);
          toast.success('Favorites reordered');
        } catch (error) {
          toast.error('Failed to reorder favorites');
          console.error('Failed to reorder favorites:', error);
        }
      }
    }
  };

  useEffect(() => {
    // Fetch services on mount
    fetchServices();
  }, [fetchServices]);

  // Loading state
  if (loading) {
    return (
      <div className="min-h-screen bg-background p-8 flex items-center justify-center"> {/* @allowed-inline */}
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-12 w-12 animate-spin text-primary" />
          <p className="text-muted-foreground">Loading services...</p>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="min-h-screen bg-background p-8"> {/* @allowed-inline */}
        <TkCard variant="destructive">
          <TkCardHeader>
            <div className="flex items-center gap-3">
              <AlertCircle className="h-5 w-5" />
              <TkCardTitle>Failed to load services</TkCardTitle>
            </div>
          </TkCardHeader>
          <TkCardContent>
            <p className="text-sm">{error}</p>
          </TkCardContent>
        </TkCard>
      </div>
    );
  }

  // Main dashboard content
  return (
    <div className="min-h-screen bg-background p-8"> {/* @allowed-inline */}
      {/* Header row */}
      <div className="flex justify-between items-center mb-4">
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 cursor-pointer">
            <span className="text-sm text-muted-foreground">Compact Mode</span>
            <TkSwitch
              checked={compactMode}
              onCheckedChange={handleCompactModeChange}
            />
          </label>
        </div>
        <div>
          <TkButton
            variant="ghost"
            size="sm"
            onClick={handleSync}
            disabled={syncing}
          >
            <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
            Sync Services
          </TkButton>
        </div>
      </div>

      {/* Dashboard content based on route */}
      {isFavoritesView ? (
        // Favorites View
        <>
          {favoriteServices.length === 0 ? (
            <TkCard>
              <TkCardContent standalone className="flex items-center gap-3">
                <Star className="h-6 w-6 text-muted-foreground" />
                <p className="text-muted-foreground">No favorite services yet. Click the star icon on any service to add it to favorites.</p>
              </TkCardContent>
            </TkCard>
          ) : (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={favoriteServices.map((s) => s.id)}
                strategy={rectSortingStrategy}
              >
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                  {favoriteServices.map((service) => (
                    <SortableServiceCard
                      key={service.id}
                      service={service}
                      variant="favorite"
                      compact={compactMode}
                      onToggleFavorite={handleToggleFavorite}
                      onShowDetails={handleShowDetails}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          )}
        </>
      ) : (
        // All Services or Category View
        <div>
          {/* Service cards grid */}
          {filteredServices.length === 0 ? (
            <TkCard>
              <TkCardContent standalone className="text-center text-muted-foreground">
                No services found
              </TkCardContent>
            </TkCard>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {filteredServices.map((service) => (
                <ServiceCard
                  key={service.id}
                  service={service}
                  variant="full"
                  compact={compactMode}
                  onToggleFavorite={handleToggleFavorite}
                  onShowDetails={handleShowDetails}
                  onRestart={handleRestart}
                  onToggleService={handleToggleService}
                  onHealthCheck={handleHealthCheck}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Restart Confirmation Dialog */}
      <TkControlledConfirmDialog
        open={serviceToRestart !== null}
        onOpenChange={(open) => !open && setServiceToRestart(null)}
        title="Restart Service"
        description={serviceToRestart ? `Are you sure you want to restart ${serviceToRestart.name}? This will temporarily interrupt the service.` : ''}
        variant="destructive"
        confirmText="Restart"
        onConfirm={confirmRestart}
      />
    </div>
  );
}
