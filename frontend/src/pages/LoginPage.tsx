import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { redirectToLogin, isAuthenticated } from '@/lib/auth';
import { TkLoader } from 'thinkube-style/components/feedback';

export default function LoginPage() {
  const navigate = useNavigate();
  const hasExecuted = useRef(false);

  useEffect(() => {
    if (hasExecuted.current) return;
    hasExecuted.current = true;

    // If already authenticated, go to dashboard instead
    if (isAuthenticated()) {
      navigate('/dashboard', { replace: true });
      return;
    }

    redirectToLogin();
  }, [navigate]);

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
