import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { handleAuthCallback } from '@/lib/auth';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkLoader } from 'thinkube-style/components/feedback';

export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = searchParams.get('code');
    const errorParam = searchParams.get('error');

    if (errorParam) {
      setError(`Authentication error: ${errorParam}`);
      return;
    }

    if (!code) {
      setError('No authorization code received');
      return;
    }

    // Exchange code for token
    handleAuthCallback(code)
      .then(() => {
        // Check for intended route
        const intendedRoute = sessionStorage.getItem('intendedRoute');
        sessionStorage.removeItem('intendedRoute');

        // Redirect to intended route or dashboard
        navigate(intendedRoute || '/dashboard');
      })
      .catch((err) => {
        console.error('Failed to handle auth callback:', err);
        setError('Failed to complete authentication. Please try again.');
      });
  }, [searchParams, navigate]);

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center"> {/* @allowed-inline */}
        <div className="text-center">
          <h1 className="text-2xl font-semibold mb-4 text-destructive">Authentication Failed</h1>
          <p className="text-muted-foreground mb-4">{error}</p>
          <TkButton onClick={() => navigate('/login')}>
            Try Again
          </TkButton>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen items-center justify-center"> {/* @allowed-inline */}
      <div className="text-center">
        <h1 className="text-2xl font-semibold mb-4">Completing authentication...</h1>
        <TkLoader />
        <p className="text-muted-foreground mt-4">Please wait...</p>
      </div>
    </div>
  );
}
