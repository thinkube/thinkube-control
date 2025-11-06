import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { TkAppLayout, type TkNavItem } from 'thinkube-style';
import { LayoutDashboard, Boxes, Layers, Container, Puzzle, Shield, Sliders, Lock, Key, Star, Grid2X2 } from 'lucide-react';
import './globals.css';

// Components
import RequireAuth from './components/RequireAuth';
import { ThemeProvider } from './components/ThemeProvider';
import { ThemeToggle } from './components/ThemeToggle';
import { UserMenu } from './components/UserMenu';

// Pages
import HomePage from './pages/HomePage';
import LoginPage from './pages/LoginPage';
import AuthCallbackPage from './pages/AuthCallbackPage';
import DashboardPage from './pages/DashboardPage';

const navigationItems: TkNavItem[] = [
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
    const path = routes[id];
    if (path) navigate(path);
  };

  // Determine active item from current path
  const getActiveItem = () => {
    const path = location.pathname;
    if (path === '/' || path === '/dashboard/favorites') return 'favorites';
    if (path === '/dashboard/all') return 'all-services';
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
    if (path === '/' || path === '/dashboard/favorites') return 'Favorites';
    if (path === '/dashboard/all') return 'All Services';
    if (path.startsWith('/templates')) return 'Templates';
    if (path.startsWith('/harbor-images')) return 'Harbor Images';
    if (path.startsWith('/optional-components')) return 'Optional Components';
    if (path.startsWith('/jupyterhub-config')) return 'JupyterHub Config';
    if (path.startsWith('/secrets')) return 'Secrets';
    if (path.startsWith('/tokens')) return 'API Tokens';
    return 'Favorites';
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
      </Routes>
    </TkAppLayout>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
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
