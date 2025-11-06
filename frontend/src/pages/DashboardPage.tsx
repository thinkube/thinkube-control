import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useServicesStore } from '@/stores/useServicesStore';
import { AlertCircle, Loader2, RefreshCw, Star } from 'lucide-react';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkSwitch } from 'thinkube-style/components/forms-inputs';
import { ServiceCard } from '@/components/ServiceCard';
import type { Service } from '@/stores/useServicesStore';

export default function DashboardPage() {
  const location = useLocation();
  const {
    services,
    loading,
    error,
    fetchServices,
    syncServices,
    getFavoriteServicesComputed,
    getFilteredServices,
    getCategories,
    categoryFilter,
    setCategoryFilter,
    toggleFavorite,
    toggleService,
    restartService,
    triggerHealthCheck,
  } = useServicesStore();
  const [compactMode, setCompactMode] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // Determine view based on route
  const isAllServicesView = location.pathname === '/dashboard/all';

  // Get categories and filtered services
  const categories = getCategories();
  const filteredServices = getFilteredServices();
  const favoriteServices = getFavoriteServicesComputed();

  // Service card handlers
  const handleToggleFavorite = async (service: Service) => {
    try {
      await toggleFavorite(service);
      console.log('Favorite toggled'); // Will be replaced with toast in Task 10
    } catch (error) {
      console.error('Failed to toggle favorite:', error);
    }
  };

  const handleShowDetails = (service: Service) => {
    // Will navigate to service details route in Task 12
    console.log('Show details for:', service.name);
  };

  const handleRestart = async (service: Service) => {
    try {
      await restartService(service.id);
      console.log('Service restarted'); // Will be replaced with toast in Task 10
    } catch (error) {
      console.error('Failed to restart service:', error);
    }
  };

  const handleToggleService = async (service: Service, enabled: boolean) => {
    try {
      await toggleService(service.id, enabled);
      console.log('Service toggled'); // Will be replaced with toast in Task 10
    } catch (error) {
      console.error('Failed to toggle service:', error);
    }
  };

  const handleHealthCheck = async (service: Service) => {
    try {
      await triggerHealthCheck(service.id);
      console.log('Health check triggered'); // Will be replaced with toast in Task 10
    } catch (error) {
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
      console.log('Services synced successfully'); // Will be replaced with toast in Task 10
    } catch (error) {
      console.error('Failed to sync services:', error); // Will be replaced with toast in Task 10
    } finally {
      setSyncing(false);
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
      {isAllServicesView ? (
        // All Services View
        <div>
          {/* Category filters */}
          <div className="mb-4 flex gap-2 flex-wrap">
            <TkButton
              variant={categoryFilter === null ? 'default' : 'outline'}
              size="sm"
              onClick={() => setCategoryFilter(null)}
            >
              All
            </TkButton>
            {categories.map((category) => (
              <TkButton
                key={category}
                variant={categoryFilter === category ? 'default' : 'outline'}
                size="sm"
                onClick={() => setCategoryFilter(category)}
              >
                {category}
              </TkButton>
            ))}
          </div>

          {/* Service cards grid */}
          {filteredServices.length === 0 ? (
            <TkCard>
              <TkCardContent className="py-8 text-center text-muted-foreground">
                No services found {categoryFilter && `in category "${categoryFilter}"`}
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
      ) : (
        // Favorites View
        <>
          {favoriteServices.length === 0 ? (
            <TkCard>
              <TkCardContent className="flex items-center gap-3 py-8">
                <Star className="h-6 w-6 text-muted-foreground" />
                <p className="text-muted-foreground">No favorite services yet. Click the star icon on any service to add it to favorites.</p>
              </TkCardContent>
            </TkCard>
          ) : (
            <div>
              {/* Favorites grid - drag-drop will be added in Task 9 */}
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {favoriteServices.map((service) => (
                  <ServiceCard
                    key={service.id}
                    service={service}
                    variant="favorite"
                    compact={compactMode}
                    onToggleFavorite={handleToggleFavorite}
                    onShowDetails={handleShowDetails}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
