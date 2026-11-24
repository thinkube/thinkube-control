/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useEffect, useRef } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TkLoader } from 'thinkube-style/components/feedback';
import { redirectToLogin, isAuthenticated } from '@/lib/auth';

export default function LoginPage() {
  const location = useLocation();
  const { t } = useTranslation();
  const hasRedirected = useRef(false);
  const authed = isAuthenticated();
  const from = (location.state as { from?: string })?.from;

  useEffect(() => {
    if (authed || hasRedirected.current) return;
    hasRedirected.current = true;

    redirectToLogin(from).catch(() => {
      hasRedirected.current = false;
    });
  }, [authed, from]);

  if (authed) {
    return <Navigate to={from || '/'} replace />;
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <div className="text-center">
        <h1 className="text-2xl font-semibold mb-4">{t('auth.redirecting')}</h1>
        <TkLoader />
        <p className="text-muted-foreground mt-4">{t('auth.redirectingMessage')}</p>
      </div>
    </div>
  );
}
