"""
Service Discovery ConfigMap Generator
Generates properly formatted service.yaml content for Kubernetes ConfigMaps
used by the Thinkube service discovery system
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import yaml
import logging

from app.core.api_tokens import get_current_user_dual_auth

logger = logging.getLogger(__name__)
router = APIRouter(tags=["service-discovery-config"])


DEFAULT_HEALTH_PATH = "/health"


class Container(BaseModel):
    name: str
    health: Optional[str] = None


class Route(BaseModel):
    path: str
    to: str


class Deployment(BaseModel):
    gateway_managed: bool = False


class ServiceDiscoveryConfigRequest(BaseModel):
    app_name: str
    app_host: str
    k8s_namespace: str
    template_url: str
    project_description: Optional[str] = ""
    deployment_date: str
    containers: List[Container]
    routes: List[Route] = []
    deployment: Deployment = Deployment()


def resolve_health_path(
    containers: List[Container], routes: List[Route]
) -> str:
    """Return the health path of the container serving the app host root.

    The web endpoint points at the app host root, so its health check must use
    the health path of whichever container is routed at "/". Falls back to the
    single container's path when there is only one, then to DEFAULT_HEALTH_PATH.
    """
    root_container = next((r.to for r in routes if r.path == "/"), None)

    if root_container is None and len(containers) == 1:
        root_container = containers[0].name

    for container in containers:
        if container.name == root_container:
            return container.health or DEFAULT_HEALTH_PATH

    return DEFAULT_HEALTH_PATH


@router.post(
    "/service-discovery/generate-configmap-yaml",
    response_model=Dict[str, Any],
    operation_id="generate_service_discovery_yaml",
)
async def generate_service_discovery_yaml(
    request: ServiceDiscoveryConfigRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    Generate service.yaml content for Thinkube service discovery ConfigMap.

    This endpoint generates properly formatted YAML that will be placed in a
    ConfigMap with label thinkube.io/managed=true for automatic service discovery.
    """

    logger.info(f"Generating service discovery YAML for app: {request.app_name}")

    # Build resources list
    resources = []
    for container in request.containers:
        resources.append(
            {
                "resource_type": "deployment",
                "resource_name": f"{request.app_name}-{container.name}",
            }
        )

    health_path = resolve_health_path(request.containers, request.routes)
    gateway_managed = request.deployment.gateway_managed

    # Build service data
    service_data = {
        "service": {
            "name": request.app_name,
            "display_name": request.app_name.replace("-", " ").title(),
            "description": request.project_description
            or f"User application deployed from {request.template_url}",
            "type": "user_app",
            "category": "applications",
            "icon": "/icons/tk_dashboard.svg",
            "endpoints": [
                {
                    "name": "web",
                    "type": "http",
                    "url": f"https://{request.app_host}",
                    "health_url": f"https://{request.app_host}{health_path}",
                    "description": "Main application endpoint",
                    "primary": True,
                }
            ],
            "dependencies": [],
            "scaling": {
                "resources": resources,
                "namespace": request.k8s_namespace,
                # Gateway-managed backends sit at zero replicas until a model is
                # loaded, so a floor of 1 would misreport them as scaled down.
                "min_replicas": 0 if gateway_managed else 1,
                "can_disable": True,
                "gateway_managed": gateway_managed,
            },
            "metadata": {
                "template_url": request.template_url,
                "deployment_date": request.deployment_date,
                "deployed_by": "thinkube-control",
            },
        }
    }

    # Convert to YAML string
    yaml_content = yaml.dump(service_data, default_flow_style=False, sort_keys=False)

    logger.info(f"Generated {len(yaml_content)} bytes of YAML for {request.app_name}")

    return {"yaml_content": yaml_content, "service_data": service_data}
