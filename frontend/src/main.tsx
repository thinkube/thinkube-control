import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { TkAppLayout } from 'thinkube-style';
import './globals.css';

// Components
import RequireAuth from './components/RequireAuth';
import { UserMenu } from './components/UserMenu';

// Pages
import HomePage from './pages/HomePage';
import LoginPage from './pages/LoginPage';
import AuthCallbackPage from './pages/AuthCallbackPage';
import DashboardPage from './pages/DashboardPage';

function AppContent() {
  const location = useLocation();
  const navigate = useNavigate();

  const handleNavClick = (id: string) => {
    const routes: Record<string, string> = {
      dashboard: '/dashboard',
      services: '/services',
      harbor: '/harbor',
      tokens: '/tokens',
      components: '/components',
      settings: '/settings',
    };
    const path = routes[id] || '/';
    navigate(path);
  };

  return (
    <TkAppLayout
      activeItem={location.pathname.split('/')[1] || 'dashboard'}
      onItemClick={handleNavClick}
      topBarContent={<UserMenu />}
    >
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
      </Routes>
    </TkAppLayout>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
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
  </React.StrictMode>
);
