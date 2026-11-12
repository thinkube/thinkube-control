/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useEffect, type ReactNode } from 'react';
import { Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TkAppLayout, type TkNavItem } from 'thinkube-style';
import { HardDrive, Settings, Key } from 'lucide-react';
import { ThemeToggle } from '@/components/ThemeToggle';
import { LanguageMenu } from '@/components/LanguageMenu';
import { UserMenu } from '@/components/UserMenu';
import StoragePage from '@/pages/StoragePage';
import ApiTokensPage from '@/pages/ApiTokensPage';
import NotFoundPage from '@/pages/NotFoundPage';
import { publicValue } from '@/lib/publicConfig';

/** Route each navigation item points at, keyed by its id. */
const NAV_ROUTES: Record<string, string> = {
  storage: '/',
  'api-tokens': '/tokens',
};

export default function App() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();

  const navigationItems: TkNavItem[] = [
    {
      id: 'workspace',
      label: t('nav.workspace'),
      lucideIcon: HardDrive,
      isGroup: true,
      children: [{ id: 'storage', label: t('nav.home'), lucideIcon: HardDrive, href: '/' }],
    },
    {
      id: 'settings',
      label: t('nav.settings'),
      lucideIcon: Settings,
      isGroup: true,
      children: [
        { id: 'api-tokens', label: t('nav.apiTokens'), lucideIcon: Key, href: '/tokens' },
      ],
    },
  ];

  const activeItem = location.pathname.startsWith('/tokens') ? 'api-tokens' : 'storage';
  const pageTitle = activeItem === 'api-tokens' ? t('apiTokens.title') : t('nav.home');

  // The deployment names the application; the translated default is what a
  // developer sees running it locally.
  const appTitle = publicValue('APP_TITLE') || t('app.title');

  useEffect(() => {
    document.title = appTitle;
  }, [appTitle]);

  return (
    <TkAppLayout
      navigationItems={navigationItems}
      activeItem={activeItem}
      onItemClick={(id) => {
        const path = NAV_ROUTES[id];
        if (path) navigate(path);
      }}
      logoText={appTitle}
      topBarTitle={pageTitle}
      topBarContent={
        <div className="flex items-center gap-2">
          <LanguageMenu />
          <ThemeToggle />
          <UserMenu />
        </div>
      }
    >
      <AppRoutes>
        <Route path="/tokens" element={<ApiTokensPage />} />
      </AppRoutes>
    </TkAppLayout>
  );
}

/** The app's pages, the same whether the app runs on its own or, as an extension, inside Thinkube Control. */
export function AppRoutes({ children }: { children?: ReactNode }) {
  return (
    <Routes>
      <Route path="/" element={<StoragePage />} />
      {children}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
