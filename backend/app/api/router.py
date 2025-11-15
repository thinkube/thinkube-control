# app/api/router.py
from fastapi import APIRouter
from app.api import (
    auth,
    dashboards,
    cicd_postgres,
    cicd_sources,
    tokens,
    websocket_executor,
    websocket_harbor,
    templates,
    debug,
    services,
    service_discovery_config,
    secrets,
    resource_status,
    optional_components,
    harbor_images,
    jupyter_images,
    cluster_resources,
    custom_images,
    jupyterhub_config,
    model_mirrors,
)

api_router = APIRouter()

# Include the routes from the different modules
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(dashboards.router, prefix="/dashboards", tags=["dashboards"])
api_router.include_router(services.router, prefix="/services", tags=["services"])
api_router.include_router(cicd_postgres.router, prefix="/cicd", tags=["cicd"])
api_router.include_router(cicd_sources.router, prefix="/cicd", tags=["cicd-sources"])
api_router.include_router(tokens.router, prefix="/tokens", tags=["api-tokens"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(
    service_discovery_config.router, prefix="/config", tags=["service-discovery-config"]
)
api_router.include_router(secrets.router, prefix="/secrets", tags=["secrets"])
api_router.include_router(resource_status.router, prefix="/resource-status", tags=["resource-status"])
api_router.include_router(optional_components.router, prefix="/optional-components", tags=["optional-components"])
api_router.include_router(harbor_images.router, tags=["harbor-images"])
api_router.include_router(jupyter_images.router, tags=["jupyter-images"])
api_router.include_router(cluster_resources.router, tags=["cluster-resources"])
api_router.include_router(custom_images.router, tags=["custom-images"])
api_router.include_router(jupyterhub_config.router, tags=["jupyterhub-config"])
api_router.include_router(model_mirrors.router, prefix="/models", tags=["models"])
api_router.include_router(debug.router, tags=["debug"])

# Include WebSocket routes (no prefix for WebSocket endpoints)
api_router.include_router(websocket_executor.router)
api_router.include_router(websocket_harbor.router)
