/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { TkAppLayout, type TkNavItem } from 'thinkube-style';
import { LayoutDashboard, Boxes, Layers, Container, Puzzle, Shield, Sliders, Lock, Key, Star, Grid2X2, Server, Code, BarChart3, Database, Cpu, FileText, Box, Zap, Radio, Bell, HardDrive } from 'lucide-react';
import { Toaster } from 'sonner';
import './globals.css';
import 'thinkube-style/styles.css';

// Components
import RequireAuth from './components/RequireAuth';
import { ThemeProvider } from './components/ThemeProvider';
import { ThemeToggle } from './components/ThemeToggle';
import { UserMenu } from './components/UserMenu';
import ErrorBoundary from './components/ErrorBoundary';
import { DownloadIndicator } from './components/DownloadIndicator';
import { RunsIndicator } from './components/RunsIndicator';
import { FixesIndicator } from './components/FixesIndicator';

// Pages
import HomePage from './pages/HomePage';
import ExtensionPage from './pages/ExtensionPage';
import { extensionBase, listExtensions, type Extension } from './lib/extensions';
import { extensionIcon } from './lib/extensionIcon';
import LoginPage from './pages/LoginPage';
import AuthCallbackPage from './pages/AuthCallbackPage';
import DashboardPage from './pages/DashboardPage';
import ServiceDetailsPage from './pages/ServiceDetailsPage';
import PodDetailsPage from './pages/PodDetailsPage';
import ApiTokensPage from './pages/ApiTokensPage';
import SecretsPage from './pages/SecretsPage';
import OptionalComponentsPage from './pages/OptionalComponentsPage';
import JupyterHubConfigPage from './pages/JupyterHubConfigPage';
import JupyterKernelsPage from './pages/JupyterKernelsPage';
import Templates from './pages/Templates';
import ModelsPage from './pages/ModelsPage';
import { HarborImages } from './pages/HarborImages';
import { ImageMirrorDeployment } from './pages/ImageMirrorDeployment';
import KnativeServicesPage from './pages/KnativeServicesPage';
import NodesPage from './pages/NodesPage';
import StoragePage from './pages/StoragePage';
import LLMGatewayPage from './pages/LLMGatewayPage';
import NewsAndFixesPage from './pages/NewsAndFixesPage';

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
      { id: "knative-services", label: "Knative Services", lucideIcon: Zap, href: "/knative-services" },
    ],
  },
  {
    id: "deployment",
    label: "Deployment & Infrastructure",
    lucideIcon: Boxes,
    isGroup: true,
    children: [
      { id: "llm-gateway", label: "LLM Gateway", lucideIcon: Radio, href: "/llm-gateway" },
      { id: "models", label: "AI Models", lucideIcon: Database, href: "/models" },
      { id: "templates", label: "Templates", lucideIcon: Layers, href: "/templates" },
      { id: "harbor-images", label: "Harbor Images", lucideIcon: Container, href: "/harbor-images" },
      { id: "optional-components", label: "Optional Components", lucideIcon: Puzzle, href: "/optional-components" },
      { id: "nodes", label: "Cluster Nodes", lucideIcon: Server, href: "/nodes" },
      { id: "storage", label: "Storage", lucideIcon: HardDrive, href: "/storage" },
    ],
  },
  {
    id: "config",
    label: "Configuration & Security",
    lucideIcon: Shield,
    isGroup: true,
    children: [
      { id: "jupyter-kernels", label: "Jupyter Kernels", lucideIcon: Code, href: "/jupyter-kernels" },
      { id: "jupyterhub-config", label: "JupyterHub Config", lucideIcon: Sliders, href: "/jupyterhub-config" },
      { id: "secrets", label: "Secrets", lucideIcon: Lock, href: "/secrets" },
      { id: "api-tokens", label: "API Tokens", lucideIcon: Key, href: "/tokens" },
      { id: "news", label: "News & Fixes", lucideIcon: Bell, href: "/news" },
    ],
  },
];

function AppContent() {
  const location = useLocation();
  const navigate = useNavigate();
  const { getCategories, fetchServices, services } = useServicesStore();
  const [navigationItems, setNavigationItems] = useState<TkNavItem[]>(baseNavigationItems);
  const [extensions, setExtensions] = useState<Extension[]>([]);

  // Fetch services on mount
  useEffect(() => {
    fetchServices();
  }, [fetchServices]);

  // The extensions service discovery found; each gets its own group in the navigation
  useEffect(() => {
    listExtensions()
      .then(setExtensions)
      .catch((e) => console.error('The extensions could not be listed:', e));
  }, []);

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

    const extensionGroups: TkNavItem[] = extensions.map((extension) => ({
      id: `x:${extension.name}`,
      label: extension.title,
      lucideIcon: extension.icon ? extensionIcon(extension.icon) : Puzzle,
      isGroup: true,
      children: extension.nav.map((entry, i) => ({
        id: `x:${extension.name}:${i}`,
        label: entry.label,
        lucideIcon: entry.icon ? extensionIcon(entry.icon) : undefined,
        href: `${extensionBase(extension.name)}${entry.path === '/' ? '' : entry.path}`,
      })),
    }));

    setNavigationItems([
      updatedDashboard,
      ...extensionGroups,
      ...baseNavigationItems.slice(1),
    ]);
  }, [services, getCategories, extensions]);

  // The extension whose page is shown, and the navigation entry whose path matches most of the address
  const shownExtension = extensions.find((e) => location.pathname.startsWith(extensionBase(e.name)));
  const extensionEntry = () => {
    if (!shownExtension) return undefined;
    const below = location.pathname.slice(extensionBase(shownExtension.name).length) || '/';
    let best: number | undefined;
    shownExtension.nav.forEach((entry, i) => {
      const matches = entry.path === '/' ? below === '/' : below === entry.path || below.startsWith(`${entry.path}/`);
      if (matches && (best === undefined || entry.path.length > shownExtension.nav[best].path.length)) best = i;
    });
    return best;
  };

  const handleNavClick = (id: string) => {
    const routes: Record<string, string> = {
      favorites: '/dashboard/favorites',
      'all-services': '/dashboard/all',
      'llm-gateway': '/llm-gateway',
      models: '/models',
      templates: '/templates',
      'knative-services': '/knative-services',
      'harbor-images': '/harbor-images',
      'optional-components': '/optional-components',
      'nodes': '/nodes',
      storage: '/storage',
      'jupyter-kernels': '/jupyter-kernels',
      'jupyterhub-config': '/jupyterhub-config',
      secrets: '/secrets',
      'api-tokens': '/tokens',
      news: '/news',
    };

    if (id.startsWith('x:')) {
      const [, name, index] = id.split(':');
      const extension = extensions.find((e) => e.name === name);
      const entry = extension?.nav[Number(index)];
      if (entry) navigate(`${extensionBase(name)}${entry.path === '/' ? '' : entry.path}`);
      return;
    }

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
    if (shownExtension) {
      const entry = extensionEntry();
      return entry === undefined ? `x:${shownExtension.name}` : `x:${shownExtension.name}:${entry}`;
    }
    if (path === '/' || path === '/dashboard/favorites') return 'favorites';
    if (path === '/dashboard/all') return 'all-services';
    if (path.startsWith('/dashboard/category/')) {
      const category = path.split('/').pop();
      return `category-${category}`;
    }
    if (path.startsWith('/llm-gateway')) return 'llm-gateway';
    if (path.startsWith('/models')) return 'models';
    if (path.startsWith('/templates')) return 'templates';
    if (path.startsWith('/knative-services')) return 'knative-services';
    if (path.startsWith('/harbor-images')) return 'harbor-images';
    if (path.startsWith('/optional-components')) return 'optional-components';
    if (path.startsWith('/nodes')) return 'nodes';
    if (path.startsWith('/storage')) return 'storage';
    if (path.startsWith('/jupyter-kernels')) return 'jupyter-kernels';
    if (path.startsWith('/jupyterhub-config')) return 'jupyterhub-config';
    if (path.startsWith('/secrets')) return 'secrets';
    if (path.startsWith('/tokens')) return 'api-tokens';
    if (path.startsWith('/news')) return 'news';
    return 'favorites';
  };

  // Determine page title from current path
  const getPageTitle = () => {
    const path = location.pathname;
    if (shownExtension) {
      const entry = extensionEntry();
      return entry === undefined ? shownExtension.title : `${shownExtension.title} - ${shownExtension.nav[entry].label}`;
    }
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
    if (path.startsWith('/llm-gateway')) return 'LLM Gateway';
    if (path.startsWith('/models')) return 'AI Models';
    if (path.startsWith('/templates')) return 'Templates';
    if (path.startsWith('/knative-services')) return 'Knative Services';
    if (path.startsWith('/harbor-images')) return 'Harbor Images';
    if (path.startsWith('/optional-components')) return 'Optional Components';
    if (path.startsWith('/nodes')) return 'Cluster Nodes';
    if (path.startsWith('/storage')) return 'Storage';
    if (path.startsWith('/jupyter-kernels')) return 'Jupyter Kernels';
    if (path.startsWith('/jupyterhub-config')) return 'JupyterHub Config';
    if (path.startsWith('/secrets')) return 'Secrets';
    if (path.startsWith('/tokens')) return 'API Tokens';
    if (path.startsWith('/news')) return 'News & Fixes';
    return 'Dashboard - Favorites';
  };

  return (
    <TkAppLayout
      navigationItems={navigationItems}
      activeItem={getActiveItem()}
      onItemClick={handleNavClick}
      logoText="Thinkube Control"
      topBarTitle={getPageTitle()}
      topBarContent={
        <div className="flex items-center gap-3">
          <FixesIndicator />
          <RunsIndicator />
          <DownloadIndicator />
          <ThemeToggle />
          <UserMenu />
        </div>
      }
    >
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/dashboard/favorites" element={<DashboardPage />} />
        <Route path="/dashboard/all" element={<DashboardPage />} />
        <Route path="/dashboard/category/:category" element={<DashboardPage />} />
        <Route path="/services/:id" element={<ErrorBoundary><ServiceDetailsPage /></ErrorBoundary>} />
        <Route path="/services/:id/pods/:podName" element={<PodDetailsPage />} />
        <Route path="/llm-gateway" element={<LLMGatewayPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/tokens" element={<ApiTokensPage />} />
        <Route path="/secrets" element={<SecretsPage />} />
        <Route path="/optional-components" element={<OptionalComponentsPage />} />
        <Route path="/jupyter-kernels" element={<JupyterKernelsPage />} />
        <Route path="/jupyterhub-config" element={<JupyterHubConfigPage />} />
        <Route path="/templates" element={<Templates />} />
        <Route path="/knative-services" element={<KnativeServicesPage />} />
        <Route path="/nodes" element={<NodesPage />} />
        <Route path="/storage" element={<StoragePage />} />
        <Route path="/news" element={<NewsAndFixesPage />} />
        <Route path="/harbor-images" element={<HarborImages />} />
        <Route path="/image-mirror/:source" element={<ImageMirrorDeployment />} />
        <Route path="/x/:name/*" element={<ExtensionPage extensions={extensions} />} />
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
