import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { isAuthenticated } from '@/lib/auth';
import { TkLoader } from 'thinkube-style/components/feedback';

export default function HomePage() {
  const navigate = useNavigate();
  const hasNavigated = useRef(false);

  useEffect(() => {
    if (hasNavigated.current) return;
    hasNavigated.current = true;

    // Redirect to dashboard if authenticated, otherwise to login
    if (isAuthenticated()) {
      navigate('/dashboard', { replace: true });
    } else {
      navigate('/login', { replace: true });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-screen items-center justify-center"> {/* @allowed-inline */}
      <TkLoader />
    </div>
  );
}
