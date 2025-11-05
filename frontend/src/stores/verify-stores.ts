// Verification file to ensure all stores can be imported
// This file is for compile-time verification only

import {
  useAuthStore,
  useServicesStore,
  useHarborStore,
  useTokensStore,
  useComponentsStore,
  useDashboardStore,
  type Service,
  type HarborImage,
  type APIToken,
  type OptionalComponent,
  type Dashboard
} from './index';

// Verify all stores export callable hooks
export function verifyStores() {
  // All stores should be callable as React hooks
  const authStore = useAuthStore;
  const servicesStore = useServicesStore;
  const harborStore = useHarborStore;
  const tokensStore = useTokensStore;
  const componentsStore = useComponentsStore;
  const dashboardStore = useDashboardStore;

  // Verify store methods exist
  const auth = useAuthStore.getState();
  const services = useServicesStore.getState();
  const harbor = useHarborStore.getState();
  const tokens = useTokensStore.getState();
  const components = useComponentsStore.getState();
  const dashboard = useDashboardStore.getState();

  // Verify key actions exist
  if (
    typeof auth.fetchUser === 'function' &&
    typeof services.fetchServices === 'function' &&
    typeof harbor.fetchImages === 'function' &&
    typeof tokens.fetchTokens === 'function' &&
    typeof components.listComponents === 'function' &&
    typeof dashboard.fetchDashboards === 'function'
  ) {
    console.log('✅ All 6 Zustand stores verified successfully');
    return true;
  }

  throw new Error('Store verification failed');
}
