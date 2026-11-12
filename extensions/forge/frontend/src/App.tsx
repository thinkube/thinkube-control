/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useEffect, type ReactNode } from 'react';
import { Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TkAppLayout, type TkNavItem } from 'thinkube-style';
import { CalendarClock, Flame, Gauge, Key, ListChecks, Plus, Settings } from 'lucide-react';
import { ThemeToggle } from '@/components/ThemeToggle';
import { LanguageMenu } from '@/components/LanguageMenu';
import { UserMenu } from '@/components/UserMenu';
import RunsPage from '@/pages/RunsPage';
import NewRunPage from '@/pages/NewRunPage';
import RunPage from '@/pages/RunPage';
import SlotsPage from '@/pages/SlotsPage';
import PrioritiesPage from '@/pages/PrioritiesPage';
import ComparePage from '@/pages/ComparePage';
import ApiTokensPage from '@/pages/ApiTokensPage';
import NotFoundPage from '@/pages/NotFoundPage';
import { publicValue } from '@/lib/publicConfig';

/** Route each navigation item points at, keyed by its id. */
const NAV_ROUTES: Record<string, string> = {
  runs: '/',
  'new-run': '/runs/new',
  slots: '/slots',
  priorities: '/priorities',
  'api-tokens': '/tokens',
};

/** The navigation item and the title of the page at a path. */
function pageOf(pathname: string): { item: string; title: string } {
  if (pathname.startsWith('/tokens')) return { item: 'api-tokens', title: 'apiTokens.title' };
  if (pathname === '/runs/new') return { item: 'new-run', title: 'nav.newRun' };
  if (pathname.startsWith('/slots')) return { item: 'slots', title: 'nav.slots' };
  if (pathname.startsWith('/priorities')) return { item: 'priorities', title: 'nav.priorities' };
  return { item: 'runs', title: 'nav.runs' };
}

export default function App() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();

  const navigationItems: TkNavItem[] = [
    {
      id: 'forge',
      label: t('nav.forge'),
      lucideIcon: Flame,
      isGroup: true,
      children: [
        { id: 'runs', label: t('nav.runs'), lucideIcon: ListChecks, href: '/' },
        { id: 'new-run', label: t('nav.newRun'), lucideIcon: Plus, href: '/runs/new' },
        { id: 'slots', label: t('nav.slots'), lucideIcon: CalendarClock, href: '/slots' },
      ],
    },
    {
      id: 'settings',
      label: t('nav.settings'),
      lucideIcon: Settings,
      isGroup: true,
      children: [
        { id: 'priorities', label: t('nav.priorities'), lucideIcon: Gauge, href: '/priorities' },
        { id: 'api-tokens', label: t('nav.apiTokens'), lucideIcon: Key, href: '/tokens' },
      ],
    },
  ];

  const page = pageOf(location.pathname);
  const activeItem = page.item;
  const pageTitle = t(page.title);

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

/** Forge's pages, the same whether Forge runs on its own or inside Thinkube Control. */
export function AppRoutes({ children }: { children?: ReactNode }) {
  return (
    <Routes>
      <Route path="/" element={<RunsPage />} />
      <Route path="/runs/new" element={<NewRunPage />} />
      <Route path="/runs/:runId" element={<RunPage />} />
      <Route path="/slots" element={<SlotsPage />} />
      <Route path="/priorities" element={<PrioritiesPage />} />
      <Route path="/compare" element={<ComparePage />} />
      {children}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
