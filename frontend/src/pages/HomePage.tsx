import { Navigate } from 'react-router-dom';
import { isAuthenticated } from '@/lib/auth';

export default function HomePage() {
  // Declarative redirect based on auth status
  return isAuthenticated() ? (
    <Navigate to="/dashboard" replace />
  ) : (
    <Navigate to="/login" replace />
  );
}
