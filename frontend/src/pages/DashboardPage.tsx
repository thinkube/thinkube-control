import { useEffect } from 'react';
import { useAuthStore } from '@/stores/useAuthStore';
import { TkCard, TkCardHeader, TkCardTitle, TkCardContent } from 'thinkube-style/components/cards-data';

export default function DashboardPage() {
  const { user, fetchUser } = useAuthStore();

  useEffect(() => {
    // Fetch user info if not already loaded
    if (!user) {
      fetchUser().catch((error) => {
        console.error('Failed to fetch user info:', error);
      });
    }
    // Only fetch when user changes from null to populated
  }, [user, fetchUser]);

  return (
    <div className="min-h-screen bg-background p-8"> {/* @allowed-inline */}
      <h1 className="text-3xl font-bold mb-4">Dashboard</h1>
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
