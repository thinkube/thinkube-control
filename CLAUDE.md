# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Thinkube Control is the management interface for the Thinkube platform -- a Kubernetes-based infrastructure for AI/ML workloads. It provides a web UI for managing services, deploying templates, mirroring container images, configuring JupyterHub, managing AI models, and more. The application is a Copier template that gets deployed into a Kubernetes cluster.

## Development Commands

### Backend (FastAPI + Python)

```bash
# Run backend locally (requires env vars from .env or cluster context)
cd backend && uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Run tests (requires PostgreSQL in cluster)
cd backend && ./run_tests.sh

# Run tests directly with pytest
cd backend && pytest tests/ -v --cov=app --cov-report=term-missing

# Run a single test file
cd backend && pytest tests/test_api_endpoints.py -v

# Lint (non-blocking)
cd backend && flake8 app/ --max-line-length=120 --exclude=__pycache__

# Format check
cd backend && black --check app/
```

### Frontend (React + Vite + TypeScript)

```bash
# Install dependencies
cd frontend && npm ci

# Dev server (port 3000)
cd frontend && npm run dev

# Build for production
cd frontend && npm run build

# Lint
cd frontend && npm run lint

# Preview production build
cd frontend && npm run preview
```

### Deployment Workflow

Changes must follow this exact sequence:

1. Edit files in this repo (`/home/thinkube/thinkube-platform/thinkube-control/`)
2. Commit and push to GitHub (`git push origin main`)
3. Deploy: `cd /home/thinkube/thinkube-platform/thinkube && ./scripts/tk_ansible ansible/40_thinkube/core/thinkube-control/12_deploy_dev.yaml`

Copier syncs from GitHub to the runtime location (`/home/thinkube/thinkube-control/`), then a webhook triggers the Argo Workflow build and ArgoCD deploys automatically. Do not manually copy files to the runtime location or edit files there directly.

## Architecture

### Backend (`backend/`)

FastAPI application using the factory pattern (`create_app()` in `app/__init__.py`).

- **`app/api/`** -- API route handlers. Each file is a router module (auth, services, templates, harbor_images, etc.) aggregated in `router.py`. Includes WebSocket endpoints for real-time execution streaming (`websocket_executor.py`, `websocket_harbor.py`).
- **`app/services/`** -- Business logic layer. Key services: `K8sServiceManager` (Kubernetes operations), `ServiceDiscovery` (discovers cluster services), `HealthCheckService` (background health monitoring), `BackgroundExecutor` (async task execution), `HarborClient` (Harbor registry API), `ModelDownloader` (AI model management).
- **`app/models/`** -- SQLAlchemy ORM models and Pydantic schemas. Models cover services, container images, deployments, favorites, Jupyter venvs.
- **`app/core/`** -- Configuration (`config.py` uses pydantic-settings), security/auth (`security.py`), API token management.
- **`app/db/`** -- Database session management. Uses PostgreSQL (`thinkube_control`) for app data. `init_services.py`/`init_images.py`/`init_venvs.py` seed initial data on startup.
- **`fastapi-mcp-extended/`** -- Custom MCP (Model Context Protocol) server integration. Exposes API endpoints as MCP resources and tools at `/mcp`.

Configuration is driven entirely by environment variables (see `app/core/config.py` for required vars). Auth is Keycloak OAuth2/OIDC.

### Frontend (`frontend/`)

React 19 SPA built with Vite, using `thinkube-style` design system library.

- **`src/main.tsx`** -- App entry point with route definitions. Uses `BrowserRouter`, `RequireAuth` wrapper for protected routes, and `TkAppLayout` from thinkube-style for navigation shell.
- **`src/pages/`** -- Page components: Dashboard, Templates, HarborImages, ModelsPage, SecretsPage, OptionalComponentsPage, JupyterHubConfigPage, etc.
- **`src/components/`** -- Shared UI components: PlaybookExecutor/BuildExecutor (WebSocket-based execution UIs), ServiceCard, TemplateParameterForm, RequireAuth, ErrorBoundary.
- **`src/stores/`** -- Zustand stores for state management: useServicesStore, useHarborStore, useAuthStore, useComponentsStore, useModelDownloadsStore, etc.
- **`src/lib/`** -- Utilities: `axios.ts` (API client with token refresh interceptor, base URL `/api/v1`), `auth.ts` (Keycloak OAuth flow), `tokenManager.ts` (localStorage token management).

Path alias: `@/` maps to `src/`. Styling: Tailwind CSS v4 + Radix UI primitives.

### Authentication

Keycloak OAuth2/OIDC flow. See `AUTHENTICATION.md` for full details. Key pattern: use `<Navigate>` (declarative) for auth redirects, never `navigate()` in useEffect (causes infinite loops in strict mode). All protected routes wrap in `<RequireAuth>`.

### Ansible (`ansible/`)

Minimal Ansible roles still used at runtime:

- **`roles/container_deployment/image_mirror/`** -- Harbor image mirroring (used by `mirror-image.yaml` playbook).
- **`roles/keycloak/`** -- Keycloak client/user/realm configuration for deployed apps.
- **`roles/gitea/`** -- Gitea repository management.
- **`roles/common/`** -- Shared utilities.

Template deployment is handled entirely by `scripts/deploy_application.py` (Python), not Ansible. The old Ansible deployment playbook and its roles have been removed.

### Playbooks (`playbooks/`)

- **`mirror-image.yaml`** -- Harbor image mirroring playbook (the only remaining playbook).

### Templates (`templates/`)

- **`k8s/`** -- Jinja2 templates for generating Kubernetes manifests: deployments (single/separate), services, HTTPRoute, Knative services, DomainMapping, build workflows, storage PVCs, MLflow secrets.
- **`service-configmap.yaml.j2`** -- Service discovery configuration template.

### K8s Manifests (`k8s/`)

Jinja templates (`.jinja` suffix) for thinkube-control's own Kubernetes deployment, processed by Copier during deployment. Includes backend/frontend deployments, services, ingress, RBAC, build workflows, Kustomization.

### Copier Template System

This repo is a Copier template (`copier.yaml`). Variables like `domain_name`, `namespace`, `registry_subdomain` are substituted into `.jinja` files during deployment. The `.copier-answers.yml.jinja` tracks applied answers. Never edit the runtime copy at `/home/thinkube/thinkube-control/`.

## Key Patterns

- **Single database**: Main app DB (`thinkube_control`) with SQLAlchemy ORM. CI/CD data is queried directly from Kubernetes (Argo Workflows).
- **WebSocket execution**: Template deployments and image mirroring stream output via WebSocket to the frontend (`websocket_executor.py`). Frontend components `PlaybookExecutor` and `BuildExecutor` consume these streams.
- **Background tasks**: Lifespan-managed background tasks for health checks (every service, periodic) and service discovery (every 5 minutes).
- **MLflow injection**: All deployed applications automatically receive MLflow auth credentials as environment variables.
- **Base images**: Backend uses `python-base:3.12-slim` and frontend uses `node-base:22-alpine` from Harbor registry. Dependencies are pre-installed in base images, not in the app Dockerfiles.
