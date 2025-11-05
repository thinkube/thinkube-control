# Authentication Pattern

## Overview

Thinkube Control uses OAuth2/OpenID Connect authentication via Keycloak. This document describes the authentication flow and implementation patterns used in the React frontend.

## Architecture

### Components

1. **RequireAuth** - Wrapper component for protected routes
2. **Auth Library** (`lib/auth.ts`) - Authentication utilities
3. **Token Manager** (`lib/tokenManager.ts`) - Token storage and expiry management
4. **Auth Store** (`stores/useAuthStore.ts`) - Zustand store for user state

### Flow Diagram

```
User visits /dashboard
    ↓
RequireAuth checks isAuthenticated()
    ↓
Not authenticated? → Navigate to /login (saves intended route)
    ↓
LoginPage redirects to Keycloak
    ↓
User authenticates with Keycloak
    ↓
Keycloak redirects to /auth/callback?code=xxx
    ↓
AuthCallbackPage exchanges code for token
    ↓
Token saved to localStorage
    ↓
Navigate to intended route or /dashboard
    ↓
RequireAuth passes → DashboardPage renders
    ↓
DashboardPage fetches user info from backend
```

## Implementation Pattern

### Protected Routes (Declarative Pattern)

We use **declarative navigation** with the `<Navigate>` component instead of imperative `navigate()` calls in `useEffect`. This prevents infinite loops in React strict mode.

#### ❌ WRONG - Imperative Pattern (causes infinite loops)

```tsx
// DON'T DO THIS
export default function DashboardPage() {
  const navigate = useNavigate();

  useEffect(() => {
    if (!isAuthenticated()) {
      navigate('/login'); // Causes infinite loop!
    }
  }, [navigate]);

  return <div>Dashboard</div>;
}
```

**Problems:**
- React strict mode double-renders components
- `navigate()` changes location, triggering re-render
- New render creates new `navigate` function
- useEffect runs again → infinite loop
- Browser throttles navigation to prevent hanging

#### ✅ CORRECT - Declarative Pattern

```tsx
// DO THIS
import RequireAuth from '@/components/RequireAuth';

// In main.tsx routes
<Route
  path="/dashboard"
  element={
    <RequireAuth>
      <DashboardPage />
    </RequireAuth>
  }
/>
```

**RequireAuth component:**

```tsx
export default function RequireAuth({ children }) {
  const location = useLocation();
  const authed = isAuthenticated();

  if (!authed) {
    // Declarative redirect - happens during render, not in useEffect
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
```

**Benefits:**
- Auth check happens during render, not in effect
- No useEffect, no useNavigate, no infinite loops
- React Router handles navigation properly
- Works correctly with strict mode double-rendering
- Saves intended route for post-login redirect

### Page Components

#### HomePage (Simple Redirect)

```tsx
export default function HomePage() {
  return isAuthenticated() ? (
    <Navigate to="/dashboard" replace />
  ) : (
    <Navigate to="/login" replace />
  );
}
```

**Declarative redirect** based on auth status. No effects, no hooks.

#### LoginPage (External Redirect)

```tsx
export default function LoginPage() {
  const location = useLocation();
  const hasRedirected = useRef(false);

  // Declarative redirect if already authenticated
  if (isAuthenticated()) {
    const from = location.state?.from || '/dashboard';
    return <Navigate to={from} replace />;
  }

  useEffect(() => {
    // Only redirect to Keycloak once
    if (hasRedirected.current) return;
    hasRedirected.current = true;

    const from = location.state?.from;
    redirectToLogin(from); // External redirect to Keycloak
  }, [location]);

  return <div>Redirecting to Keycloak...</div>;
}
```

**Why useEffect here?**
- `redirectToLogin()` performs `window.location.href = keycloakUrl` (external redirect)
- Cannot be done declaratively in render
- useRef prevents double-redirect in strict mode

#### AuthCallbackPage (Token Exchange)

```tsx
export default function AuthCallbackPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const hasExecuted = useRef(false);

  useEffect(() => {
    if (hasExecuted.current) return;
    hasExecuted.current = true;

    const code = searchParams.get('code');

    handleAuthCallback(code)
      .then(() => {
        const intendedRoute = sessionStorage.getItem('intendedRoute');
        navigate(intendedRoute || '/dashboard', { replace: true });
      });
  }, [searchParams, navigate]);

  return <div>Completing authentication...</div>;
}
```

**Why useEffect here?**
- Must perform async token exchange with backend
- Cannot be done during render (side effect)
- useRef prevents double token exchange in strict mode
- searchParams and navigate in deps (safe - only changes on callback)

#### DashboardPage (No Auth Check)

```tsx
export default function DashboardPage() {
  const { user, fetchUser } = useAuthStore();

  useEffect(() => {
    if (!user) {
      fetchUser();
    }
  }, [user, fetchUser]);

  return <div>Welcome {user?.name}</div>;
}
```

**No authentication check** - handled by `RequireAuth` wrapper.
Just fetches user data from backend.

## Token Management

### Storage

Tokens are stored in `localStorage`:
- `access_token` - JWT access token
- `refresh_token` - Refresh token (optional)
- `token_expiry` - Calculated expiry timestamp

### Authentication Check

```typescript
export const isAuthenticated = (): boolean => {
  const token = getToken();
  return !!(token && !isTokenExpired());
};
```

Checks both token existence and expiry.

### Token Refresh

The axios interceptor in `lib/axios.ts` automatically refreshes expired tokens:
1. Request fails with 401
2. Attempt to refresh using refresh token
3. Retry original request with new token
4. If refresh fails, redirect to login

## Smart Redirects

### Saving Intended Route

When `RequireAuth` redirects to login, it saves the intended route:

```tsx
<Navigate to="/login" replace state={{ from: location.pathname }} />
```

### Restoring After Login

`LoginPage` passes the intended route to Keycloak redirect:

```tsx
const from = location.state?.from;
redirectToLogin(from); // Saves to sessionStorage
```

After callback, `AuthCallbackPage` restores it:

```tsx
const intendedRoute = sessionStorage.getItem('intendedRoute');
navigate(intendedRoute || '/dashboard');
```

**Result:** User visits `/settings` → redirected to login → after auth, returns to `/settings`

## Best Practices

### ✅ DO

1. **Use RequireAuth wrapper** for all protected routes
2. **Use declarative `<Navigate>`** for auth-based redirects
3. **Check auth during render**, not in useEffect
4. **Use useRef guards** only for external redirects or side effects
5. **Save intended route** for post-login redirect
6. **Keep tokens in localStorage** (secure over HTTPS)

### ❌ DON'T

1. **Don't call navigate() in useEffect** for auth checks (infinite loops)
2. **Don't add navigate to dependency arrays** (causes re-renders)
3. **Don't add Zustand functions to deps** (recreated every render)
4. **Don't check auth in every component** (use RequireAuth wrapper)
5. **Don't store tokens in state** (lost on refresh)

## References

- [React Router Protected Routes](https://ui.dev/react-router-protected-routes-authentication)
- [Authentication with React Router v6](https://blog.logrocket.com/authentication-react-router-v6/)
- [React Router Navigating](https://reactrouter.com/start/framework/navigating)
- [React Strict Mode](https://react.dev/reference/react/StrictMode)

## Troubleshooting

### Infinite Loop / Maximum Update Depth Exceeded

**Symptom:** Console shows "Maximum update depth exceeded" and browser throttles navigation.

**Cause:** Using `navigate()` in useEffect with `navigate` in dependency array.

**Fix:** Use declarative `<Navigate>` component or RequireAuth wrapper instead.

### Double Token Exchange

**Symptom:** Backend receives two token exchange requests on callback.

**Cause:** React strict mode double-renders in development.

**Fix:** Use useRef guard in AuthCallbackPage to prevent second execution.

### Token Not Persisting

**Symptom:** User logged out on page refresh.

**Cause:** Tokens not being saved to localStorage.

**Fix:** Ensure `storeTokens()` is called in `handleAuthCallback()`.

### Redirect Loop

**Symptom:** Browser redirects between /login and /dashboard repeatedly.

**Cause:** `isAuthenticated()` returning wrong value or RequireAuth logic error.

**Fix:** Check token storage, expiry calculation, and RequireAuth conditions.
