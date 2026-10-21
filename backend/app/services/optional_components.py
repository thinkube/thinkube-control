# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
Service for managing optional Thinkube components
"""

import logging
import time
import yaml
from typing import Dict, List, Any, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.services.metadata_fetcher import (
    _USER_METADATA_REF,
    CatalogUnavailableError,
    _extract,
    _PLATFORM_METADATA_REPO,
    _PLATFORM_ORG,
    _fetch_json,
    _get_github_config,
    _github_url,
    invalidated_at,
    platform_ref,
    reads_direct,
)

logger = logging.getLogger(__name__)

# Component catalog: the platform's optional_components.json from
# thinkube-metadata at the platform's release ref, and the user's from
# <user>-metadata at main, cached in memory together. A playbook component
# runs a playbook of the thinkube checkout, so only the platform catalog may
# declare one; a user component installs from a template, like the inference
# backends.
_COMPONENTS_CATALOG_FILE = "optional_components.json"
# The fields every component entry is read by.
_REQUIRED_FIELDS = ("display_name", "description", "category", "requirements", "namespace")
_COMPONENTS_CATALOG_CACHE: Optional[Dict[str, Any]] = None
_COMPONENTS_CATALOG_CACHE_TIME: float = 0
_COMPONENTS_CATALOG_TTL: float = 300  # 5 minutes



def get_components_catalog() -> Dict[str, Any]:
    """
    Get the components catalog, fetching from thinkube-metadata when the
    in-memory copy is older than the TTL.

    Raises:
        CatalogUnavailableError: the catalog could not be fetched or read.
    """
    global _COMPONENTS_CATALOG_CACHE, _COMPONENTS_CATALOG_CACHE_TIME

    now = time.time()
    if (
        _COMPONENTS_CATALOG_CACHE is not None
        and (now - _COMPONENTS_CATALOG_CACHE_TIME) < _COMPONENTS_CATALOG_TTL
        and _COMPONENTS_CATALOG_CACHE_TIME >= invalidated_at()
    ):
        return _COMPONENTS_CATALOG_CACHE

    import urllib.error
    direct = reads_direct()
    catalog_url = _github_url(
        _PLATFORM_ORG, _PLATFORM_METADATA_REPO, platform_ref(), _COMPONENTS_CATALOG_FILE, direct
    )
    try:
        data = _fetch_json(catalog_url, token=_get_github_config()[1] if direct else None)
    except urllib.error.HTTPError as e:
        raise CatalogUnavailableError(f"Cannot fetch catalog {catalog_url}: HTTP {e.code} {e.reason}") from e
    components = data.get("components") if isinstance(data, dict) else None
    if not isinstance(components, dict):
        raise CatalogUnavailableError(
            f"Catalog {catalog_url} has no 'components' mapping"
        )

    user_components = _user_components(components)
    merged = {**components, **user_components}

    _COMPONENTS_CATALOG_CACHE = merged
    _COMPONENTS_CATALOG_CACHE_TIME = now
    logger.info(
        f"Fetched components catalog: {len(components)} platform components, "
        f"{len(user_components)} user components"
    )
    return merged


def _user_components(platform: Dict[str, Any]) -> Dict[str, Any]:
    """The components of the user's metadata repository, each checked.

    The repository is <GITHUB_USERNAME>-metadata, read at main with the
    user's token. No file there means no user components. An entry that
    reuses a platform component's name, has no template.url, declares
    playbooks or lacks a field the components page reads makes the catalog
    unavailable, with the entry named.
    """
    github_username, github_token = _get_github_config()
    if not (github_username and github_token):
        return {}
    import urllib.error
    url = _github_url(
        github_username, f"{github_username}-metadata", _USER_METADATA_REF, _COMPONENTS_CATALOG_FILE,
        reads_direct(),
    )
    try:
        data = _fetch_json(url, token=github_token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise CatalogUnavailableError(f"Cannot fetch catalog {url}: HTTP {e.code} {e.reason}") from e
    entries = _extract(data, "components", url, "dict")
    checked = {}
    for name, entry in entries.items():
        if name in platform:
            raise CatalogUnavailableError(
                f"{url}: component '{name}' has the name of a platform component; give it its own"
            )
        if not isinstance(entry, dict) or not (entry.get("template") or {}).get("url"):
            raise CatalogUnavailableError(
                f"{url}: component '{name}' has no template.url; a user component installs from a template"
            )
        if "playbooks" in entry:
            raise CatalogUnavailableError(
                f"{url}: component '{name}' declares playbooks; only platform components run playbooks"
            )
        missing = [f for f in _REQUIRED_FIELDS if f not in entry]
        if missing:
            raise CatalogUnavailableError(f"{url}: component '{name}' lacks {', '.join(missing)}")
        # The template deploy puts the component in a namespace named after it.
        app_name = entry["template"].get("fixed_name") or name
        if entry["namespace"] != app_name:
            raise CatalogUnavailableError(
                f"{url}: component '{name}' declares namespace '{entry['namespace']}', but it is "
                f"deployed into '{app_name}' (template.fixed_name, or the component's name)"
            )
        checked[name] = {**entry, "source": "user"}
    return checked


class OptionalComponentService:
    """Manage installation and status of optional Thinkube components"""

    def __init__(self, db: Session = None):
        """Initialize the optional component service"""
        self.db = db
        self._init_kubernetes()
        
    def _init_kubernetes(self):
        """Initialize Kubernetes client"""
        try:
            # Try in-cluster config first (when running in pod)
            config.load_incluster_config()
        except config.ConfigException:
            try:
                # Fall back to kubeconfig file
                config.load_kube_config()
            except config.ConfigException as e:
                logger.error(f"Failed to initialize Kubernetes client: {e}")
                self.core_v1 = None
                return
                
        self.core_v1 = client.CoreV1Api()
        
    ARCH_LABEL = "kubernetes.io/arch"

    def _cluster_architectures(self) -> set:
        """Architectures the cluster's nodes actually offer."""
        try:
            from kubernetes import client

            nodes = client.CoreV1Api().list_node()
            return {
                n.metadata.labels.get(self.ARCH_LABEL)
                for n in nodes.items
                if n.metadata.labels and n.metadata.labels.get(self.ARCH_LABEL)
            }
        except Exception as e:
            logger.warning(f"Could not read node architectures: {e}")
            return set()

    def _architecture_status(self, info: Dict[str, Any]) -> tuple:
        """Whether a component's architectures exist here, and which are missing.

        A component with no declared architectures runs anywhere.
        """
        required = info.get("architectures") or []
        if not required:
            return True, []

        available = self._cluster_architectures()
        if not available:
            return True, []  # Unknown cluster: do not block on a failed read.

        if set(required) & available:
            return True, []
        return False, [f"a {'/'.join(required)} node"]

    def node_selector(self, component_name: str) -> Dict[str, str]:
        """Node selector pinning a component to an architecture it supports."""
        info = get_components_catalog().get(component_name) or {}
        required = info.get("architectures") or []
        if len(required) != 1:
            return {}
        return {self.ARCH_LABEL: required[0]}

    def list_components(self) -> List[Dict[str, Any]]:
        """
        List all available optional components with their installation status
        
        Returns:
            List of component dictionaries with status information
        """
        components = []
        
        catalog = get_components_catalog()
        for name, info in catalog.items():
            installed = self._check_if_installed(name)
            component = {
                **info,
                "name": name,
                "installed": installed,
                "activity": self._activity(name),
                "component_version": self._get_component_version(name) if installed else None,
                "requirements_met": self._check_requirements(info["requirements"])
                and self._architecture_status(info)[0],
                "missing_requirements": self._get_missing_requirements(info["requirements"])
                + self._architecture_status(info)[1],
                "architectures": info.get("architectures") or [],
            }
            components.append(component)
            
        return components
    
    def get_component(self, component_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific component
        
        Args:
            component_name: Name of the component
            
        Returns:
            Component information or None if not found
        """
        catalog = get_components_catalog()
        if component_name not in catalog:
            return None

        info = catalog[component_name]
        installed = self._check_if_installed(component_name)
        return {
            **info,
            "name": component_name,
            "installed": installed,
            "activity": self._activity(component_name),
            "component_version": self._get_component_version(component_name) if installed else None,
            "requirements_met": self._check_requirements(info["requirements"])
            and self._architecture_status(info)[0],
            "architectures": info.get("architectures") or [],
            "missing_requirements": self._get_missing_requirements(info["requirements"]),
            "status": self._get_component_status(component_name)
        }
    
    def _activity(self, component_name: str) -> Optional[str]:
        """``queued`` while a run for the component waits, ``installing`` or ``uninstalling`` while it runs, else None."""
        try:
            from app.models.deployments import TemplateDeployment

            active = (
                self.db.query(TemplateDeployment)
                .filter(
                    TemplateDeployment.template_url.in_(
                        [f"optional://{component_name}", f"optional://{component_name}/uninstall"]
                    ),
                    TemplateDeployment.status.in_(["queued", "pending", "running"]),
                )
                .order_by(TemplateDeployment.created_at.desc())
                .first()
            )
        except Exception:
            return None
        if active is None:
            return None
        if active.status == "queued":
            return "queued"
        return "uninstalling" if active.template_url.endswith("/uninstall") else "installing"

    def _check_if_installed(self, component_name: str) -> bool:
        """
        Check if a component is installed by looking for its ConfigMap
        
        Args:
            component_name: Name of the component
            
        Returns:
            True if component is installed, False otherwise
        """
        if not self.core_v1:
            return False
            
        try:
            catalog = get_components_catalog()
            namespace = catalog[component_name]["namespace"]

            # Method 1: Check for the old fixed-name ConfigMap (for existing components)
            try:
                configmap = self.core_v1.read_namespaced_config_map(
                    name="thinkube-service-config",
                    namespace=namespace
                )
                if configmap:
                    return True
            except ApiException:
                pass  # Not found, try method 2
            
            # Method 2: Check for any ConfigMap with the component label (new method)
            configmaps = self.core_v1.list_namespaced_config_map(
                namespace=namespace,
                label_selector=f"thinkube.io/component={component_name}"
            )
            return len(configmaps.items) > 0
            
        except ApiException:
            return False
            
    def _get_component_version(self, component_name: str) -> Optional[str]:
        """Read component_version from the thinkube-service-config ConfigMap"""
        if not self.core_v1:
            return None

        try:
            catalog = get_components_catalog()
            if component_name not in catalog:
                return None
            namespace = catalog[component_name]["namespace"]

            configmap = self.core_v1.read_namespaced_config_map(
                name="thinkube-service-config",
                namespace=namespace
            )

            service_yaml = configmap.data.get("service.yaml") if configmap.data else None
            if service_yaml:
                parsed = yaml.safe_load(service_yaml)
                service = parsed.get("service", parsed)
                return service.get("component_version")
        except (ApiException, Exception):
            pass

        return None

    def _check_requirements(self, requirements: List[str]) -> bool:
        """
        Check if all requirements for a component are met
        
        Args:
            requirements: List of required component names
            
        Returns:
            True if all requirements are met, False otherwise
        """
        if not requirements:
            return True
            
        if not self.core_v1:
            return False
            
        for req in requirements:
            # Check if required service is running
            if not self._is_service_running(req):
                return False
                
        return True
    
    def _get_missing_requirements(self, requirements: List[str]) -> List[str]:
        """
        Get list of missing requirements
        
        Args:
            requirements: List of required component names
            
        Returns:
            List of missing requirement names
        """
        if not requirements:
            return []
            
        missing = []
        for req in requirements:
            if not self._is_service_running(req):
                missing.append(req)
                
        return missing
    
    def _is_service_running(self, service_name: str) -> bool:
        """
        Check if a service is running
        
        Args:
            service_name: Name of the service to check
            
        Returns:
            True if service is running, False otherwise
        """
        # Core components that are always assumed to be installed
        # These are part of the base Thinkube platform
        CORE_COMPONENTS = [
            "keycloak",
            "harbor",
            "postgresql",
            "gitea",
            "argocd",
            "argo-workflows",
            "jupyterhub",
            "mlflow",
            "seaweedfs",
            "juicefs"
        ]
        
        # If it's a core component, assume it's running
        # Core components are managed by the installer, not optional components
        if service_name in CORE_COMPONENTS:
            return True
            
        # For truly optional components, check if they're installed
        if not self.core_v1:
            return False

        # Look up actual namespace from component definition
        # (component name might differ from namespace name, e.g. prometheus -> monitoring)
        catalog = get_components_catalog()
        if service_name in catalog:
            namespace = catalog[service_name]["namespace"]
        else:
            # Fallback: assume service name = namespace name
            namespace = service_name

        # Check if the optional component itself is installed
        # by looking for its namespace
        try:
            ns = self.core_v1.read_namespace(namespace)
            if not ns:
                return False

            # Check for running pods in the namespace
            pods = self.core_v1.list_namespaced_pod(namespace=namespace)
            running_pods = [p for p in pods.items if p.status.phase == "Running"]

            return len(running_pods) > 0

        except ApiException:
            return False
    
    def _get_component_status(self, component_name: str) -> Dict[str, Any]:
        """
        Get detailed status of a component
        
        Args:
            component_name: Name of the component
            
        Returns:
            Status dictionary with deployment information
        """
        if not self.core_v1:
            return {"status": "unknown", "message": "Kubernetes API not available"}
            
        catalog = get_components_catalog()
        namespace = catalog[component_name]["namespace"]

        try:
            # Check namespace
            ns = self.core_v1.read_namespace(namespace)
            if not ns:
                return {"status": "not_installed", "message": "Namespace does not exist"}
                
            # Get pods in namespace
            pods = self.core_v1.list_namespaced_pod(namespace=namespace)
            
            total_pods = len(pods.items)
            running_pods = len([p for p in pods.items if p.status.phase == "Running"])
            failed_pods = len([p for p in pods.items if p.status.phase == "Failed"])
            
            if total_pods == 0:
                return {"status": "not_installed", "message": "No pods found"}
            elif running_pods == total_pods:
                return {
                    "status": "running",
                    "message": f"All {total_pods} pods running",
                    "pods": {
                        "total": total_pods,
                        "running": running_pods,
                        "failed": failed_pods
                    }
                }
            elif failed_pods > 0:
                return {
                    "status": "error",
                    "message": f"{failed_pods} pods failed",
                    "pods": {
                        "total": total_pods,
                        "running": running_pods,
                        "failed": failed_pods
                    }
                }
            else:
                return {
                    "status": "partial",
                    "message": f"{running_pods}/{total_pods} pods running",
                    "pods": {
                        "total": total_pods,
                        "running": running_pods,
                        "failed": failed_pods
                    }
                }
                
        except ApiException as e:
            if e.status == 404:
                return {"status": "not_installed", "message": "Component not found"}
            else:
                return {"status": "error", "message": str(e)}
    
    def template_descriptor(self, component_name: str) -> Optional[Dict[str, Any]]:
        """The template a component deploys from, when it is not playbook-backed.

        Components install two ways: the platform ones run an ansible playbook,
        the inference backends go through the template deployment process. The
        listing does not care which; only install does.
        """
        component = get_components_catalog().get(component_name) or {}
        descriptor = component.get("template")
        if not descriptor or not descriptor.get("url"):
            return None
        return descriptor

    def get_playbook_path(self, component_name: str, playbook_type: str) -> Optional[str]:
        """
        Get the full path to a component's playbook
        
        Args:
            component_name: Name of the component
            playbook_type: Type of playbook (install, test, uninstall)
            
        Returns:
            Full path to the playbook or None if not found
        """
        catalog = get_components_catalog()
        if component_name not in catalog:
            return None

        component = catalog[component_name]
        # Template-backed components carry no playbooks; the caller branches on
        # template_descriptor instead.
        playbook_name = (component.get("playbooks") or {}).get(playbook_type)
        
        if not playbook_name:
            return None
            
        # Construct path relative to the ansible directory
        return f"ansible/40_thinkube/optional/{component_name}/{playbook_name}"
    
    def validate_installation(self, component_name: str) -> Dict[str, Any]:
        """
        Validate if a component can be installed
        
        Args:
            component_name: Name of the component
            
        Returns:
            Validation result with status and messages
        """
        catalog = get_components_catalog()
        if component_name not in catalog:
            return {
                "valid": False,
                "error": f"Component '{component_name}' not found"
            }

        component = catalog[component_name]
        
        # Check if already installed
        if self._check_if_installed(component_name):
            return {
                "valid": False,
                "error": f"Component '{component_name}' is already installed"
            }
            
        # Check requirements
        missing_reqs = self._get_missing_requirements(component["requirements"])
        if missing_reqs:
            return {
                "valid": False,
                "error": f"Missing requirements: {', '.join(missing_reqs)}"
            }
            
        return {
            "valid": True,
            "message": f"Component '{component_name}' can be installed"
        }