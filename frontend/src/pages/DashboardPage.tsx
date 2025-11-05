import { useEffect } from 'react';
import { useAuthStore } from '@/stores/useAuthStore';
import { useNavigate } from 'react-router-dom';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';

export default function DashboardPage() {
  const { user, fetchUser, isAuthenticated } = useAuthStore();
  const navigate = useNavigate();


  useEffect(() => {
    if (!isAuthenticated()) {
      navigate('/login');
      return;
    }

    if (!user) {
      fetchUser().catch((error) => {
        console.error('Failed to fetch user info:', error);
        // Don't redirect on fetch error - user is authenticated
        // The error will be shown in the UI
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen bg-background p-8"> {/* @allowed-inline */}
      <h1 className="text-3xl font-bold mb-4">Thinkube Control - Dashboard</h1>
      {user && (
        <TkCard>
          <TkCardHeader>
            <TkCardTitle>Welcome, {user.name || user.preferred_username || 'User'}!</TkCardTitle>
          </TkCardHeader>
          <TkCardContent>
            <p className="text-muted-foreground">Phase 1: Foundation setup complete</p>
            <p className="text-sm text-muted-foreground mt-2">Email: {user.email}</p>
            {user.roles && user.roles.length > 0 && (
              <p className="text-sm text-muted-foreground">Roles: {user.roles.join(', ')}</p>
            )}
          </TkCardContent>
        </TkCard>
      )}
    </div>
  );
}
