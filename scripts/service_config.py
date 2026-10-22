# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The discovery ConfigMap of an application, rendered into its k8s/.

Thinkube Control finds an application by the ConfigMap labelled
thinkube.io/managed in its namespace, and the LLM gateway reads a backend
type's spec.llm_backend from the same ConfigMap. Both generators of k8s/
write it from thinkube.yaml, so Argo CD applies it with the rest of the app on
every sync: it exists whether or not Thinkube Control was running when the
build ended.

The values that come from the deploy rather than from thinkube.yaml (template,
template version, description, deploy date) are written into the ConfigMap;
a later regeneration reads them back from the ConfigMap in the cluster.
"""

from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel

DEFAULT_HEALTH_PATH = "/health"

# The startup window of a GPU container without containers[].startupSeconds,
# as the thinkube.yaml specification defines it.
DEFAULT_STARTUP_SECONDS = 600

COMPONENT_SERVICE_TYPE = "component"
COMPONENT_CATEGORY = "components"
APP_SERVICE_TYPE = "user_app"
APP_CATEGORY = "applications"

# An application's discovery ConfigMap. In Control's namespace this name is
# Control's own, so an extension's carries the extension's name.
CONFIGMAP_NAME = "thinkube-service-config"
CONFIGMAP_KEY = "service.yaml"
MANIFEST = "service-config.yaml"


class Container(BaseModel):
    name: str
    health: Optional[str] = None
    port: Optional[int] = None
    startupSeconds: Optional[int] = None


class Route(BaseModel):
    path: str
    to: str


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


class ServiceConfigRequest(BaseModel):
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


def llm_backend_descriptor(
    backend: LLMBackend, app_name: str, containers: List[Container]
) -> Dict[str, Any]:
    """The backend as the gateway reads it from the service ConfigMap.

    The base Deployment is the one the deploy names after the app and the
    container; the gateway copies it per model and probes the container's port.
    """
    container = next((c for c in containers if c.name == backend.container), None)
    if container is None or not container.port:
        raise ValueError(f"llm_backend.container '{backend.container}' names no container with a port")
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
        "startup_seconds": container.startupSeconds or DEFAULT_STARTUP_SECONDS,
    }


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


def service_data(request: ServiceConfigRequest) -> Dict[str, Any]:
    """The service.yaml content of the discovery ConfigMap."""
    resources = [
        {"resource_type": "deployment", "resource_name": f"{request.app_name}-{container.name}"}
        for container in request.containers
    ]

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

    data = {
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
        data["service"]["llm_backend"] = llm_backend_descriptor(
            request.llm_backend, request.app_name, request.containers
        )
    return data


def configmap_name(app_name: str, extension: bool) -> str:
    return f"{app_name}-service-config" if extension else CONFIGMAP_NAME


def request_for(
    *,
    app_name: str,
    domain: str,
    namespace: str,
    config: Dict[str, Any],
    template_url: str,
    template_version: str,
    project_description: str,
    deployment_date: str,
) -> ServiceConfigRequest:
    """The request for an application from its thinkube.yaml and the deploy's values."""
    spec = config.get("spec", {})
    return ServiceConfigRequest(
        app_name=app_name,
        app_host=f"{app_name}.{domain}",
        k8s_namespace=namespace,
        template_url=template_url,
        template_version=template_version,
        app_version=config.get("metadata", {}).get("version"),
        project_description=project_description,
        deployment_date=deployment_date,
        containers=spec.get("containers", []),
        routes=spec.get("routes", []),
        deployment=spec.get("deployment", {}),
        llm_backend=spec.get("llm_backend"),
    )


def manifest(request: ServiceConfigRequest, extension: bool) -> str:
    """k8s/service-config.yaml: the discovery ConfigMap of the application."""
    service_type = COMPONENT_SERVICE_TYPE if request.deployment.is_component else APP_SERVICE_TYPE
    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": configmap_name(request.app_name, extension),
            "namespace": request.k8s_namespace,
            "labels": {
                "app": request.app_name,
                "app.kubernetes.io/name": request.app_name,
                "thinkube.io/managed": "true",
                "thinkube.io/service-type": service_type,
                "thinkube.io/service-name": request.app_name,
            },
        },
        "data": {
            CONFIGMAP_KEY: yaml.dump(service_data(request), default_flow_style=False, sort_keys=False),
        },
    }
    return yaml.dump(configmap, default_flow_style=False, sort_keys=False)


def recorded_deploy(configmap_data: Optional[Dict[str, str]], where: str) -> Dict[str, str]:
    """The deploy's values a discovery ConfigMap in the cluster holds.

    ``where`` names the ConfigMap, for the error when it holds none.
    """
    content = (configmap_data or {}).get(CONFIGMAP_KEY)
    if not content:
        raise ValueError(
            f"{where} has no {CONFIGMAP_KEY}, which records the template the app was deployed "
            "from; redeploy the app from its template (redeploy_template) to write it again"
        )
    service = (yaml.safe_load(content) or {}).get("service") or {}
    metadata = service.get("metadata") or {}
    values = {
        "template_url": metadata.get("template_url"),
        "template_version": metadata.get("template_version"),
        "deployment_date": metadata.get("deployment_date"),
        "project_description": service.get("description"),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError(
            f"{where} does not record {', '.join(missing)}; redeploy the app from its template "
            "(redeploy_template) to write them again"
        )
    return values
