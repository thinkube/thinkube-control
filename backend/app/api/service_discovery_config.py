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


class Container(BaseModel):
    name: str


class ServiceDiscoveryConfigRequest(BaseModel):
    app_name: str
    app_host: str
    k8s_namespace: str
    template_url: str
    project_description: Optional[str] = ""
    deployment_date: str
    containers: List[Container]


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
                    "health_url": f"https://{request.app_host}/health",
                    "description": "Main application endpoint",
                    "primary": True,
                }
            ],
            "dependencies": [],
            "scaling": {
                "resources": resources,
                "namespace": request.k8s_namespace,
                "min_replicas": 1,
                "can_disable": True,
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
