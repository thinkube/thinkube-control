/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { ReactNode, useEffect } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TkLoader } from 'thinkube-style/components/feedback';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { isAuthenticated } from '@/lib/auth';
import { useAuthStore } from '@/stores/useAuthStore';

interface RequireAuthProps {
  children: ReactNode;
}

/**
 * Gate for the authenticated part of the app: sends anonymous visitors to
 * Keycloak, and loads the user profile the layout needs before rendering.
 */
export default function RequireAuth({ children }: RequireAuthProps) {
  const location = useLocation();
  const { t } = useTranslation();
  const { user, loading, error, fetchUser } = useAuthStore();
  const authed = isAuthenticated();

  useEffect(() => {
    if (authed && !user && !loading && !error) {
      fetchUser().catch(() => {
        // The error is kept in the store and shown below.
      });
    }
  }, [authed, user, loading, error, fetchUser]);

  if (!authed) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (!user) {
    if (error) {
      return (
        <div className="flex h-screen items-center justify-center bg-background">
          <div className="text-center">
            <h1 className="text-2xl font-semibold mb-2">{t('errors.loadingApp.title')}</h1>
            <p className="text-muted-foreground mb-4">{t('errors.loadingApp.message')}</p>
            <TkButton onClick={() => window.location.reload()}>
              {t('errors.loadingApp.reload')}
            </TkButton>
          </div>
        </div>
      );
    }

    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="text-center">
          <TkLoader />
          <p className="text-muted-foreground mt-4">{t('errors.general.loading')}</p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
