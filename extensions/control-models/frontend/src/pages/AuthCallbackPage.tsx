/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkLoader } from 'thinkube-style/components/feedback';
import { handleAuthCallback } from '@/lib/auth';

export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();
  const [exchangeFailed, setExchangeFailed] = useState(false);
  const hasExecuted = useRef(false);

  const errorParam = searchParams.get('error');
  const code = searchParams.get('code');

  // Keycloak's own error and a missing code are visible from the URL alone;
  // only a failed exchange has to wait for the request.
  const error = errorParam
    ? `${t('auth.loginFailed')}: ${errorParam}`
    : !code
      ? t('auth.noCode')
      : exchangeFailed
        ? t('auth.loginFailed')
        : null;

  useEffect(() => {
    if (errorParam || !code || hasExecuted.current) return;
    hasExecuted.current = true;

    handleAuthCallback(code)
      .then(() => {
        const intendedRoute = sessionStorage.getItem('intendedRoute');
        sessionStorage.removeItem('intendedRoute');
        navigate(intendedRoute || '/', { replace: true });
      })
      .catch(() => setExchangeFailed(true));
  }, [code, errorParam, navigate]);

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="text-center">
          <h1 className="text-2xl font-semibold mb-4 text-destructive">{t('auth.loginFailed')}</h1>
          <p className="text-muted-foreground mb-4">{error}</p>
          <TkButton onClick={() => navigate('/login')}>{t('auth.tryAgain')}</TkButton>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <div className="text-center">
        <h1 className="text-2xl font-semibold mb-4">{t('auth.loggingIn')}</h1>
        <TkLoader />
        <p className="text-muted-foreground mt-4">{t('auth.redirecting')}</p>
      </div>
    </div>
  );
}
