// src/router.js
import { createRouter, createWebHistory } from 'vue-router';
import { isAuthenticated, redirectToLogin } from './services/auth';

// Import layouts
import MainLayout from './layouts/MainLayout.vue';

// Import views (lazy loading for better performance)
const Dashboard = () => import('./views/Dashboard.vue');
const NotFound = () => import('./views/NotFound.vue');
const AuthCallback = () => import('./views/AuthCallback.vue');
const ApiTokens = () => import('./views/ApiTokens.vue');
const Templates = () => import('./views/Templates.vue');
const Secrets = () => import('./views/Secrets.vue');
const OptionalComponents = () => import('./views/OptionalComponents.vue');
const HarborImages = () => import('./views/HarborImages.vue');
const JupyterHubConfig = () => import('./views/JupyterHubConfig.vue');

// Define routes
const routes = [
  {
    path: '/auth/callback',
    name: 'auth-callback',
    component: AuthCallback,
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    component: MainLayout,
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        redirect: '/dashboard'
      },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: Dashboard
      },
      {
        path: 'tokens',
        name: 'api-tokens',
        component: ApiTokens
      },
      {
        path: 'templates',
        name: 'templates',
        component: Templates
      },
      {
        path: 'secrets',
        name: 'secrets',
        component: Secrets
      },
      {
        path: 'optional-components',
        name: 'optional-components',
        component: OptionalComponents
      },
      {
        path: 'harbor-images',
        name: 'harbor-images',
        component: HarborImages
      },
      {
        path: 'harbor-images/mirror/:deploymentId',
        name: 'image-mirror-deployment',
        component: () => import('./views/ImageMirrorDeployment.vue')
      },
      {
        path: 'jupyterhub-config',
        name: 'jupyterhub-config',
        component: JupyterHubConfig
      }
    ]
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: NotFound,
    meta: { requiresAuth: false }
  }
];

// Create router
const router = createRouter({
  history: createWebHistory(),
  routes
});

// Navigation guard to check authentication
router.beforeEach(async (to, from, next) => {
  console.log('Navigating to:', to.path, 'Requires auth:', to.meta.requiresAuth);
  
  // Skip auth check for routes that don't require it
  if (to.meta.requiresAuth === false) {
    next();
    return;
  }
  
  // Check if user is authenticated
  const authenticated = isAuthenticated();
  console.log('Is authenticated:', authenticated);
  
  if (!authenticated) {
    console.log('Not authenticated, redirecting to login');
    // Store the intended route and redirect to Keycloak login
    await redirectToLogin(to.fullPath);
    return;
  }
  
  console.log('Authenticated, proceeding to route');
  next();
});

export default router;