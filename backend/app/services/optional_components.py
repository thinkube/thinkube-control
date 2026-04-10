"""
Service for managing optional Thinkube components
"""

import json
import logging
import os
import time
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime

from sqlalchemy.orm import Session
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# Component catalog: fetched from thinkube-metadata at runtime, cached in memory
_COMPONENTS_CATALOG_URL = "https://raw.githubusercontent.com/thinkube/thinkube-metadata/main/optional_components.json"
_COMPONENTS_CATALOG_CACHE: Optional[Dict[str, Any]] = None
_COMPONENTS_CATALOG_CACHE_TIME: float = 0
_COMPONENTS_CATALOG_TTL: float = 300  # 5 minutes
_PERSISTENT_CACHE_DIR = Path(os.getenv("THINKUBE_CONTROL_HOME", "/home/thinkube-control")) / "cache"


def _save_persistent_cache(filename: str, data: dict) -> None:
    """Save fetched catalog to persistent storage so it survives pod restarts"""
    try:
        _PERSISTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = _PERSISTENT_CACHE_DIR / filename
        with open(cache_path, "w") as f:
            json.dump(data, f)
        logger.debug(f"Saved persistent cache: {cache_path}")
    except Exception as e:
        logger.warning(f"Failed to save persistent cache {filename}: {e}")


def _load_persistent_cache(filename: str) -> Optional[dict]:
    """Load catalog from persistent storage"""
    cache_path = _PERSISTENT_CACHE_DIR / filename
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load persistent cache {filename}: {e}")
    return None


def _load_bundled_components() -> Dict[str, Any]:
    """Load bundled optional_components.json fallback shipped with thinkube-control"""
    bundled = Path(__file__).parent.parent / "data" / "optional_components.json"
    if bundled.exists():
        with open(bundled) as f:
            data = json.load(f)
            return data.get("components", {})
    return {}


def get_components_catalog() -> Dict[str, Any]:
    """
    Get the components catalog, fetching from thinkube-metadata if cache expired.
    Fallback chain: memory cache → fetch → stale memory → persistent cache → bundled copy.
    """
    global _COMPONENTS_CATALOG_CACHE, _COMPONENTS_CATALOG_CACHE_TIME

    now = time.time()
    if _COMPONENTS_CATALOG_CACHE is not None and (now - _COMPONENTS_CATALOG_CACHE_TIME) < _COMPONENTS_CATALOG_TTL:
        return _COMPONENTS_CATALOG_CACHE

    try:
        import urllib.request
        req = urllib.request.Request(_COMPONENTS_CATALOG_URL, headers={"User-Agent": "thinkube-control"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            components = data.get("components", {})
            _COMPONENTS_CATALOG_CACHE = components
            _COMPONENTS_CATALOG_CACHE_TIME = now
            _save_persistent_cache("optional_components.json", data)
            logger.info(f"Fetched components catalog from thinkube-metadata: {len(components)} components")
            return components
    except Exception as e:
        logger.warning(f"Failed to fetch components catalog from thinkube-metadata: {e}")

    # Fallback to stale memory cache
    if _COMPONENTS_CATALOG_CACHE is not None:
        logger.info("Using stale cached components catalog")
        return _COMPONENTS_CATALOG_CACHE

    # Fallback to persistent cache on shared storage
    persistent = _load_persistent_cache("optional_components.json")
    if persistent:
        components = persistent.get("components", {})
        if components:
            _COMPONENTS_CATALOG_CACHE = components
            _COMPONENTS_CATALOG_CACHE_TIME = now
            logger.info(f"Using persistent cached components catalog: {len(components)} components")
            return _COMPONENTS_CATALOG_CACHE

    # Final fallback to bundled copy
    components = _load_bundled_components()
    if components:
        _COMPONENTS_CATALOG_CACHE = components
        _COMPONENTS_CATALOG_CACHE_TIME = now
        logger.info(f"Using bundled components catalog fallback: {len(components)} components")
    else:
        logger.error("No components catalog available (fetch failed, no cache, no bundled copy)")
        _COMPONENTS_CATALOG_CACHE = {}
        _COMPONENTS_CATALOG_CACHE_TIME = now

    return _COMPONENTS_CATALOG_CACHE


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
        
    def list_components(self) -> List[Dict[str, Any]]:
        """
        List all available optional components with their installation status
        
        Returns:
            List of component dictionaries with status information
        """
        components = []
        
        catalog = get_components_catalog()
        for name, info in catalog.items():
            component = {
                **info,
                "name": name,
                "installed": self._check_if_installed(name),
                "requirements_met": self._check_requirements(info["requirements"]),
                "missing_requirements": self._get_missing_requirements(info["requirements"])
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
        return {
            **info,
            "name": component_name,
            "installed": self._check_if_installed(component_name),
            "requirements_met": self._check_requirements(info["requirements"]),
            "missing_requirements": self._get_missing_requirements(info["requirements"]),
            "status": self._get_component_status(component_name)
        }
    
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
        playbook_name = component["playbooks"].get(playbook_type)
        
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