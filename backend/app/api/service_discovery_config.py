# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

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
    port: Optional[int] = None


class Route(BaseModel):
    path: str
    to: str


COMPONENT_SERVICE_TYPE = "component"
COMPONENT_CATEGORY = "components"
APP_SERVICE_TYPE = "user_app"
APP_CATEGORY = "applications"


class Deployment(BaseModel):
    type: str = "app"
    gateway_managed: bool = False

    @property
    def is_component(self) -> bool:
        return self.type == "component"


class LLMBackend(BaseModel):
    """spec.llm_backend: the component is a backend type of the LLM gateway."""

    type: str
    display_name: str
    container: str
    driver: str
    tier: str
    # The OpenAI API root, as the OpenAI API specification places it.
    api_path: str = "/v1"
    # CUDA compute capabilities ("12.1") the server runs on; empty is any GPU.
    gpu_compute_capabilities: List[str] = []
    # The server answers the Anthropic Messages API at <api_path>/messages.
    anthropic_messages: bool = False


def llm_backend_descriptor(
    backend: LLMBackend, app_name: str, containers: List[Container]
) -> Dict[str, Any]:
    """The backend as the gateway reads it from the service ConfigMap.

    The base Deployment is the one the deploy names after the app and the
    container; the gateway copies it per model and probes the container's port.
    """
    container = next((c for c in containers if c.name == backend.container), None)
    if container is None or not container.port:
        raise HTTPException(
            status_code=422,
            detail=f"llm_backend.container '{backend.container}' names no container with a port",
        )
    return {
        "type": backend.type,
        "display_name": backend.display_name,
        "driver": backend.driver,
        "tier": backend.tier,
        "deployment": f"{app_name}-{container.name}",
        "port": container.port,
        "api_path": backend.api_path,
        "health_path": container.health or DEFAULT_HEALTH_PATH,
        "gpu_compute_capabilities": backend.gpu_compute_capabilities,
        "anthropic_messages": backend.anthropic_messages,
    }


class ServiceDiscoveryConfigRequest(BaseModel):
    app_name: str
    app_host: str
    k8s_namespace: str
    template_url: str
    # The template release tag the application was generated from.
    template_version: str
    # metadata.version of the application's thinkube.yaml, when it declares one.
    app_version: Optional[str] = None
    project_description: Optional[str] = ""
    deployment_date: str
    containers: List[Container]
    routes: List[Route] = []
    deployment: Deployment = Deployment()
    llm_backend: Optional[LLMBackend] = None


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
    is_component = request.deployment.is_component

    service_type = COMPONENT_SERVICE_TYPE if is_component else APP_SERVICE_TYPE
    category = COMPONENT_CATEGORY if is_component else APP_CATEGORY
    # A component is not developed after it is deployed, so its version is the
    # template release; an app has its own version in thinkube.yaml.
    component_version = (
        request.template_version.removeprefix("v") if is_component else request.app_version
    )

    # Build service data
    service_data = {
        "service": {
            "name": request.app_name,
            "display_name": request.app_name.replace("-", " ").title(),
            "description": request.project_description
            or f"User application deployed from {request.template_url}",
            "type": service_type,
            "category": category,
            "icon": "/icons/tk_dashboard.svg",
            "component_version": component_version,
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
                "template_version": request.template_version,
                "deployment_date": request.deployment_date,
                "deployed_by": "thinkube-control",
            },
        }
    }

    if request.llm_backend is not None:
        service_data["service"]["llm_backend"] = llm_backend_descriptor(
            request.llm_backend, request.app_name, request.containers
        )

    # Convert to YAML string
    yaml_content = yaml.dump(service_data, default_flow_style=False, sort_keys=False)

    logger.info(f"Generated {len(yaml_content)} bytes of YAML for {request.app_name}")

    return {"yaml_content": yaml_content, "service_data": service_data}
