'use client';

import { useEffect } from 'react';
import { redirectToLogin } from '@/lib/auth';
import { TkLoader } from 'thinkube-style/components/feedback';

export default function LoginPage() {
  useEffect(() => {
    redirectToLogin();
  }, []);

  return (
    <div className="flex h-screen items-center justify-center"> {/* @allowed-inline */}
      <div className="text-center">
        <h1 className="text-2xl font-semibold mb-4">Redirecting to login...</h1>
        <TkLoader />
        <p className="text-muted-foreground mt-4">Please wait while we redirect you to Keycloak.</p>
      </div>
    </div>
  );
}
