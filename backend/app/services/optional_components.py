"""
Service for managing optional Thinkube components
"""

import logging
import os
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime

from sqlalchemy.orm import Session
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class OptionalComponentService:
    """Manage installation and status of optional Thinkube components"""
    
    # Hardcoded component definitions for simplicity and maintainability
    COMPONENTS = {
        "prometheus": {
            "display_name": "Prometheus",
            "description": "Metrics collection and storage for cluster monitoring",
            "category": "monitoring",
            "icon": "/icons/tk_observability.svg",
            "requirements": [],
            "namespace": "monitoring",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "perses": {
            "display_name": "Perses",
            "description": "Dashboard visualization and metrics exploration platform",
            "category": "monitoring",
            "icon": "/icons/tk_observability.svg",
            "requirements": ["keycloak", "prometheus"],
            "namespace": "perses",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "opensearch": {
            "display_name": "OpenSearch",
            "description": "Distributed search and analytics engine with log aggregation",
            "category": "data",
            "icon": "/icons/tk_data.svg",
            "requirements": [],
            "namespace": "opensearch",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "pgadmin": {
            "display_name": "PgAdmin",
            "description": "Web-based PostgreSQL database administration tool",
            "category": "data",
            "icon": "/icons/tk_data.svg",
            "requirements": ["postgresql", "keycloak"],
            "namespace": "pgadmin",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "qdrant": {
            "display_name": "Qdrant",
            "description": "High-performance vector database for AI applications",
            "category": "ai",
            "icon": "/icons/tk_vector.svg",
            "requirements": [],
            "namespace": "qdrant",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "knative": {
            "display_name": "Knative",
            "description": "Kubernetes-based platform for deploying serverless workloads",
            "category": "infrastructure",
            "icon": "/icons/tk_devops.svg",
            "requirements": [],
            "namespace": "knative-serving",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "weaviate": {
            "display_name": "Weaviate",
            "description": "Open-source vector database with GraphQL interface for AI applications (BSD-3)",
            "category": "ai",
            "icon": "/icons/tk_vector.svg",
            "requirements": ["harbor"],
            "namespace": "weaviate",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "chroma": {
            "display_name": "Chroma",
            "description": "Open-source embedding database for AI applications (Apache 2.0)",
            "category": "ai",
            "icon": "/icons/tk_vector.svg",
            "requirements": ["harbor"],
            "namespace": "chroma",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "litellm": {
            "display_name": "LiteLLM",
            "description": "Unified LLM API proxy with load balancing, cost tracking, and rate limiting",
            "category": "ai",
            "icon": "/icons/tk_ai.svg",
            "requirements": ["harbor", "keycloak", "postgresql", "seaweedfs"],
            "namespace": "litellm",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "valkey": {
            "display_name": "Valkey",
            "description": "High-performance in-memory data store, Redis-compatible (BSD-3)",
            "category": "data",
            "icon": "/icons/tk_data.svg",
            "requirements": [],
            "namespace": "valkey",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "argilla": {
            "display_name": "Argilla",
            "description": "NLP/LLM data annotation and curation platform for AI model training (Apache 2.0)",
            "category": "ai",
            "icon": "/icons/tk_design.svg",
            "requirements": ["harbor", "keycloak", "opensearch", "valkey"],
            "namespace": "argilla",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        # CVAT - Excluded for v0.1.0 (ARM64 release) - x86_64 only upstream images
        # "cvat": {
        #     "display_name": "CVAT",
        #     "description": "Computer vision annotation tool for image and video labeling (MIT)",
        #     "category": "ai",
        #     "icon": "/icons/tk_design.svg",
        #     "requirements": ["harbor", "keycloak", "postgresql", "clickhouse"],
        #     "namespace": "cvat",
        #     "playbooks": {
        #         "install": "00_install.yaml",
        #         "test": "18_test.yaml",
        #         "uninstall": "19_rollback.yaml"
        #     }
        # },
        "clickhouse": {
            "display_name": "ClickHouse",
            "description": "Real-time analytics database for OLAP workloads (Apache 2.0)",
            "category": "data",
            "icon": "/icons/tk_data.svg",
            "requirements": [],
            "namespace": "clickhouse",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "nats": {
            "display_name": "NATS",
            "description": "Real-time messaging system with JetStream for pub/sub and event-driven AI (Apache 2.0)",
            "category": "infrastructure",
            "icon": "/icons/tk_devops.svg",
            "requirements": [],
            "namespace": "nats",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        },
        "langfuse": {
            "display_name": "Langfuse",
            "description": "LLM observability platform for tracing and monitoring AI applications (MIT)",
            "category": "ai",
            "icon": "/icons/tk_observability.svg",
            "requirements": ["postgresql", "keycloak", "clickhouse", "valkey"],
            "namespace": "langfuse",
            "playbooks": {
                "install": "00_install.yaml",
                "test": "18_test.yaml",
                "uninstall": "19_rollback.yaml"
            }
        }
    }
    
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
        
        for name, info in self.COMPONENTS.items():
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
        if component_name not in self.COMPONENTS:
            return None
            
        info = self.COMPONENTS[component_name]
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
            namespace = self.COMPONENTS[component_name]["namespace"]
            
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
        if service_name in self.COMPONENTS:
            namespace = self.COMPONENTS[service_name]["namespace"]
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
            
        namespace = self.COMPONENTS[component_name]["namespace"]
        
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
        if component_name not in self.COMPONENTS:
            return None
            
        component = self.COMPONENTS[component_name]
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
        if component_name not in self.COMPONENTS:
            return {
                "valid": False,
                "error": f"Component '{component_name}' not found"
            }
            
        component = self.COMPONENTS[component_name]
        
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