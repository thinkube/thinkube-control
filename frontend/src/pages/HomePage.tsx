import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { isAuthenticated } from '@/lib/auth';
import { TkLoader } from 'thinkube-style/components/feedback';

export default function HomePage() {
  const navigate = useNavigate();

  useEffect(() => {
    // Redirect to dashboard if authenticated, otherwise to login
    if (isAuthenticated()) {
      navigate('/dashboard');
    } else {
      navigate('/login');
    }
  }, [navigate]);

  return (
    <div className="flex h-screen items-center justify-center"> {/* @allowed-inline */}
      <TkLoader />
    </div>
  );
}
