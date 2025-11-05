import { useEffect, useRef } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { redirectToLogin, isAuthenticated } from '@/lib/auth';
import { TkLoader } from 'thinkube-style/components/feedback';

export default function LoginPage() {
  const location = useLocation();
  const hasRedirected = useRef(false);

  // If already authenticated, redirect to intended route or dashboard
  if (isAuthenticated()) {
    const from = (location.state as { from?: string })?.from || '/dashboard';
    return <Navigate to={from} replace />;
  }

  useEffect(() => {
    // Redirect to Keycloak only once
    if (hasRedirected.current) return;
    hasRedirected.current = true;

    const from = (location.state as { from?: string })?.from;
    redirectToLogin(from);
  }, [location]);

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
