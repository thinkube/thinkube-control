import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { TkAppLayout, type TkNavItem } from 'thinkube-style';
import { LayoutDashboard, Boxes, Layers, Container, Puzzle, Shield, Sliders, Lock, Key, Star, Grid2X2, Server, Code, BarChart3, Database, Cpu, FileText, Box } from 'lucide-react';
import { Toaster } from 'sonner';
import './globals.css';

// Components
import RequireAuth from './components/RequireAuth';
import { ThemeProvider } from './components/ThemeProvider';
import { ThemeToggle } from './components/ThemeToggle';
import { UserMenu } from './components/UserMenu';
import ErrorBoundary from './components/ErrorBoundary';

// Pages
import HomePage from './pages/HomePage';
import LoginPage from './pages/LoginPage';
import AuthCallbackPage from './pages/AuthCallbackPage';
import DashboardPage from './pages/DashboardPage';
import ServiceDetailsPage from './pages/ServiceDetailsPage';
import PodDetailsPage from './pages/PodDetailsPage';
import ApiTokensPage from './pages/ApiTokensPage';
import SecretsPage from './pages/SecretsPage';
import OptionalComponentsPage from './pages/OptionalComponentsPage';
import JupyterHubConfigPage from './pages/JupyterHubConfigPage';
import Templates from './pages/Templates';
import { HarborImages } from './pages/HarborImages';
import { ImageMirrorDeployment } from './pages/ImageMirrorDeployment';

// Store
import { useServicesStore } from './stores/useServicesStore';

// Category icon mapping
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

const baseNavigationItems: TkNavItem[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    lucideIcon: LayoutDashboard,
    isGroup: true,
    children: [
      { id: "favorites", label: "Favorites", lucideIcon: Star, href: "/dashboard/favorites" },
      { id: "all-services", label: "All Services", lucideIcon: Grid2X2, href: "/dashboard/all" },
    ],
  },
  {
    id: "deployment",
    label: "Deployment & Infrastructure",
    lucideIcon: Boxes,
    isGroup: true,
    children: [
      { id: "templates", label: "Templates", lucideIcon: Layers, href: "/templates" },
      { id: "harbor-images", label: "Harbor Images", lucideIcon: Container, href: "/harbor-images" },
      { id: "optional-components", label: "Optional Components", lucideIcon: Puzzle, href: "/optional-components" },
    ],
  },
  {
    id: "config",
    label: "Configuration & Security",
    lucideIcon: Shield,
    isGroup: true,
    children: [
      { id: "jupyterhub-config", label: "JupyterHub Config", lucideIcon: Sliders, href: "/jupyterhub-config" },
      { id: "secrets", label: "Secrets", lucideIcon: Lock, href: "/secrets" },
      { id: "api-tokens", label: "API Tokens", lucideIcon: Key, href: "/tokens" },
    ],
  },
];

function AppContent() {
  const location = useLocation();
  const navigate = useNavigate();
  const { getCategories, fetchServices, services } = useServicesStore();
  const [navigationItems, setNavigationItems] = useState<TkNavItem[]>(baseNavigationItems);

  // Fetch services on mount
  useEffect(() => {
    fetchServices();
  }, [fetchServices]);

  // Dynamically build navigation items with categories when services change
  useEffect(() => {
    const categories = getCategories();
    const dashboardItem = baseNavigationItems[0];

    // Add category sub-items dynamically
    const categoryItems = categories.map((category) => ({
      id: `category-${category.toLowerCase()}`,
      label: category,
      lucideIcon: categoryIconMap[category.toLowerCase()] || Server,
      href: `/dashboard/category/${category.toLowerCase()}`,
    }));

    const updatedDashboard: TkNavItem = {
      ...dashboardItem,
      children: [
        ...(dashboardItem.children || []),
        ...categoryItems,
      ],
    };

    setNavigationItems([
      updatedDashboard,
      ...baseNavigationItems.slice(1),
    ]);
  }, [services, getCategories]);

  const handleNavClick = (id: string) => {
    const routes: Record<string, string> = {
      favorites: '/dashboard/favorites',
      'all-services': '/dashboard/all',
      templates: '/templates',
      'harbor-images': '/harbor-images',
      'optional-components': '/optional-components',
      'jupyterhub-config': '/jupyterhub-config',
      secrets: '/secrets',
      'api-tokens': '/tokens',
    };

    // Handle category routes
    if (id.startsWith('category-')) {
      const category = id.replace('category-', '');
      navigate(`/dashboard/category/${category}`);
      return;
    }

    const path = routes[id];
    if (path) navigate(path);
  };

  // Determine active item from current path
  const getActiveItem = () => {
    const path = location.pathname;
    if (path === '/' || path === '/dashboard/favorites') return 'favorites';
    if (path === '/dashboard/all') return 'all-services';
    if (path.startsWith('/dashboard/category/')) {
      const category = path.split('/').pop();
      return `category-${category}`;
    }
    if (path.startsWith('/templates')) return 'templates';
    if (path.startsWith('/harbor-images')) return 'harbor-images';
    if (path.startsWith('/optional-components')) return 'optional-components';
    if (path.startsWith('/jupyterhub-config')) return 'jupyterhub-config';
    if (path.startsWith('/secrets')) return 'secrets';
    if (path.startsWith('/tokens')) return 'api-tokens';
    return 'favorites';
  };

  // Determine page title from current path
  const getPageTitle = () => {
    const path = location.pathname;
    if (path === '/' || path === '/dashboard/favorites') return 'Dashboard - Favorites';
    if (path === '/dashboard/all') return 'Dashboard - All Services';
    if (path.startsWith('/dashboard/category/')) {
      const category = path.split('/').pop();
      // Capitalize first letter of each word
      const formatted = category!.split('-').map(word =>
        word.charAt(0).toUpperCase() + word.slice(1)
      ).join(' ');
      return `Dashboard - ${formatted}`;
    }
    if (path.startsWith('/templates')) return 'Templates';
    if (path.startsWith('/harbor-images')) return 'Harbor Images';
    if (path.startsWith('/optional-components')) return 'Optional Components';
    if (path.startsWith('/jupyterhub-config')) return 'JupyterHub Config';
    if (path.startsWith('/secrets')) return 'Secrets';
    if (path.startsWith('/tokens')) return 'API Tokens';
    return 'Dashboard - Favorites';
  };

  return (
    <TkAppLayout
      navigationItems={navigationItems}
      activeItem={getActiveItem()}
      onItemClick={handleNavClick}
      logoText="Thinkube Control"
      topBarTitle={getPageTitle()}
      topBarLeftContent={<ThemeToggle />}
      topBarContent={<UserMenu />}
    >
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/dashboard/favorites" element={<DashboardPage />} />
        <Route path="/dashboard/all" element={<DashboardPage />} />
        <Route path="/dashboard/category/:category" element={<DashboardPage />} />
        <Route path="/services/:id" element={<ErrorBoundary><ServiceDetailsPage /></ErrorBoundary>} />
        <Route path="/services/:id/pods/:podName" element={<PodDetailsPage />} />
        <Route path="/tokens" element={<ApiTokensPage />} />
        <Route path="/secrets" element={<SecretsPage />} />
        <Route path="/optional-components" element={<OptionalComponentsPage />} />
        <Route path="/jupyterhub-config" element={<JupyterHubConfigPage />} />
        <Route path="/templates" element={<Templates />} />
        <Route path="/harbor-images" element={<HarborImages />} />
        <Route path="/image-mirror/:source" element={<ImageMirrorDeployment />} />
      </Routes>
    </TkAppLayout>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <Toaster richColors position="top-right" />
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <AppContent />
              </RequireAuth>
            }
          />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>
);
