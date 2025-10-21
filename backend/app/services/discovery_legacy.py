"""Service discovery for Thinkube platform components"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.models.services import Service as ServiceModel
from app.models.service_schemas import ServiceType, ServiceValidation


logger = logging.getLogger(__name__)


class ServiceDiscovery:
    """Discover and sync services from Kubernetes cluster"""

    # Core services configuration
    CORE_SERVICES = [
        {
            "name": "keycloak",
            "display_name": "Keycloak",
            "namespace": "keycloak",
            "category": "infrastructure",
            "description": "Identity and access management",
            "icon": "mdi-account-key",
            "health_endpoint": "/health",
            "url_pattern": "https://auth.{domain}",
        },
        {
            "name": "harbor",
            "display_name": "Harbor Registry",
            "namespace": "registry",
            "category": "infrastructure",
            "description": "Container image registry",
            "icon": "mdi-docker",
            "health_endpoint": "/api/v2.0/health",
            "url_pattern": "https://registry.{domain}",
        },
        {
            "name": "gitea",
            "display_name": "Gitea",
            "namespace": "gitea",
            "category": "development",
            "description": "Git repository management",
            "icon": "mdi-git",
            "health_endpoint": "/api/v1/version",
            "url_pattern": "https://git.{domain}",
        },
        {
            "name": "argocd",
            "display_name": "ArgoCD",
            "namespace": "argocd",
            "category": "infrastructure",
            "description": "GitOps continuous delivery",
            "icon": "mdi-sync-circle",
            "health_endpoint": "/api/v1/session",
            "url_pattern": "https://argocd.{domain}",
        },
        {
            "name": "argo-workflows",
            "display_name": "Argo Workflows",
            "namespace": "argo",
            "category": "infrastructure",
            "description": "Workflow orchestration",
            "icon": "mdi-sitemap",
            "health_endpoint": "/api/v1/version",
            "url_pattern": "https://argo.{domain}",
            "deployment_name": "argo-workflows-server",
        },
        {
            "name": "minio",
            "display_name": "MinIO",
            "namespace": "minio",
            "category": "infrastructure",
            "description": "Object storage",
            "icon": "mdi-database",
            "health_endpoint": "/minio/health/live",
            "url_pattern": "https://minio.{domain}",
        },
        {
            "name": "postgresql",
            "display_name": "PostgreSQL",
            "namespace": "postgres",
            "category": "infrastructure",
            "description": "Relational database",
            "icon": "mdi-database",
            "health_endpoint": None,
            "url_pattern": None,
            "resource_type": "statefulset",
            "deployment_name": "postgresql-official",
        },
        {
            "name": "seaweedfs",
            "display_name": "SeaweedFS",
            "namespace": "seaweedfs",
            "category": "infrastructure",
            "description": "Distributed file system",
            "icon": "mdi-file-tree",
            "health_endpoint": "/status",
            "url_pattern": "https://seaweedfs.{domain}",
            "resource_type": "statefulset",
            "deployment_name": "seaweedfs-master",
        },
        {
            "name": "gpu-operator",
            "display_name": "GPU Operator",
            "namespace": "gpu-operator",
            "category": "infrastructure",
            "description": "NVIDIA GPU management",
            "icon": "mdi-expansion-card",
            "health_endpoint": None,
            "url_pattern": None,
            "deployment_name": "gpu-operator",
        },
        {
            "name": "thinkube-control",
            "display_name": "Thinkube Control",
            "namespace": "thinkube-control",
            "category": "infrastructure",
            "description": "Platform control center",
            "icon": "mdi-view-dashboard",
            "health_endpoint": "/health",
            "url_pattern": "https://control.{domain}",
        },
    ]

    # Optional services configuration
    OPTIONAL_SERVICES = [
        {
            "name": "prometheus",
            "display_name": "Prometheus",
            "namespace": "monitoring",
            "category": "monitoring",
            "description": "Metrics and monitoring",
            "icon": "mdi-chart-line",
            "health_endpoint": "/-/healthy",
            "url_pattern": "https://prometheus.{domain}",
            "resource_type": "statefulset",
            "deployment_name": "prometheus",
        },
        {
            "name": "grafana",
            "display_name": "Grafana",
            "namespace": "monitoring",
            "category": "monitoring",
            "description": "Metrics visualization",
            "icon": "mdi-chart-areaspline",
            "health_endpoint": "/api/health",
            "url_pattern": "https://grafana.{domain}",
            "dependencies": ["prometheus"],
            "deployment_name": "grafana",
        },
        {
            "name": "opensearch",
            "display_name": "OpenSearch",
            "namespace": "opensearch",
            "category": "monitoring",
            "description": "Search and analytics",
            "icon": "mdi-magnify",
            "health_endpoint": "/_cluster/health",
            "url_pattern": "https://opensearch.{domain}",
        },
        {
            "name": "jupyterhub",
            "display_name": "JupyterHub",
            "namespace": "jupyterhub",
            "category": "development",
            "description": "Multi-user Jupyter notebooks",
            "icon": "mdi-notebook",
            "health_endpoint": "/hub/api",
            "url_pattern": "https://jupyter.{domain}",
            "deployment_name": "hub",
        },
        {
            "name": "code-server",
            "display_name": "Code Server",
            "namespace": "code-server",
            "category": "development",
            "description": "VS Code in the browser",
            "icon": "mdi-microsoft-visual-studio-code",
            "health_endpoint": "/healthz",
            "url_pattern": "https://code.{domain}",
        },
        {
            "name": "pgadmin",
            "display_name": "pgAdmin",
            "namespace": "pgadmin",
            "category": "infrastructure",
            "description": "PostgreSQL administration",
            "icon": "mdi-database-settings",
            "health_endpoint": "/misc/ping",
            "url_pattern": "https://pgadmin.{domain}",
        },
        {
            "name": "qdrant",
            "display_name": "Qdrant",
            "namespace": "qdrant",
            "category": "infrastructure",
            "description": "Vector database",
            "icon": "mdi-vector-square",
            "health_endpoint": "/",
            "url_pattern": "https://qdrant.{domain}",
            "resource_type": "statefulset",
            "deployment_name": "qdrant",
        },
        {
            "name": "knative",
            "display_name": "Knative",
            "namespace": "knative-serving",
            "category": "infrastructure",
            "description": "Serverless platform",
            "icon": "mdi-cloud-outline",
            "health_endpoint": "/",
            "url_pattern": None,  # No UI
        },
    ]

    def __init__(self, db: Session, domain: str):
        """Initialize service discovery

        Args:
            db: Database session
            domain: Base domain for services
        """
        self.db = db
        self.domain = domain
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
                raise

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.custom_v1 = client.CustomObjectsApi()

    def discover_all(self) -> Dict[str, List[ServiceModel]]:
        """Discover all services in the cluster

        Returns:
            Dictionary with service types as keys and lists of services as values
        """
        results = {
            "core": self.discover_core_services(),
            "optional": self.discover_optional_services(),
            "user_app": self.discover_user_apps(),
        }

        # Sync to database
        self.sync_services(results)

        return results

    def discover_core_services(self) -> List[ServiceModel]:
        """Scan for core services"""
        discovered = []

        for service_config in self.CORE_SERVICES:
            service = self._check_service_deployment(service_config, "core")
            if service:
                discovered.append(service)

        return discovered

    def discover_optional_services(self) -> List[ServiceModel]:
        """Scan for optional services if deployed"""
        discovered = []

        for service_config in self.OPTIONAL_SERVICES:
            service = self._check_service_deployment(service_config, "optional")
            if service:
                discovered.append(service)

        return discovered

    def discover_user_apps(self) -> List[ServiceModel]:
        """Import user apps from ArgoCD applications"""
        discovered = []

        try:
            # Look for ArgoCD applications
            apps = self.custom_v1.list_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace="argocd",
                plural="applications",
                label_selector="app.kubernetes.io/managed-by=thinkube-control",
            )

            for app in apps.get("items", []):
                metadata = app.get("metadata", {})
                spec = app.get("spec", {})
                status = app.get("status", {})

                service = ServiceModel(
                    name=metadata.get("name"),
                    display_name=metadata.get("labels", {}).get(
                        "display-name", metadata.get("name")
                    ),
                    description=metadata.get("annotations", {}).get(
                        "description", "User application"
                    ),
                    type="user_app",
                    namespace=spec.get("destination", {}).get(
                        "namespace", metadata.get("name")
                    ),
                    category="application",
                    icon=metadata.get("labels", {}).get("icon", "mdi-application"),
                    url=self._construct_url(metadata.get("name")),
                    health_endpoint=metadata.get("annotations", {}).get(
                        "health-endpoint", "/health"
                    ),
                    is_enabled=status.get("health", {}).get("status") != "Suspended",
                    dependencies=(
                        metadata.get("annotations", {})
                        .get("dependencies", "")
                        .split(",")
                        if metadata.get("annotations", {}).get("dependencies")
                        else []
                    ),
                    service_metadata={
                        "argocd_app": metadata.get("name"),
                        "sync_status": status.get("sync", {}).get("status"),
                        "health_status": status.get("health", {}).get("status"),
                    },
                )
                discovered.append(service)

        except ApiException as e:
            if e.status != 404:  # Ignore if CRD doesn't exist
                logger.error(f"Failed to discover user apps from ArgoCD: {e}")

        return discovered

    def _check_service_deployment(
        self, service_config: Dict[str, Any], service_type: str
    ) -> Optional[ServiceModel]:
        """Check if a service is deployed in the cluster"""
        namespace = service_config["namespace"]
        name = service_config["name"]
        deployment_name = service_config.get("deployment_name", name)
        resource_type = service_config.get("resource_type", "deployment")

        try:
            # Check if namespace exists
            self.core_v1.read_namespace(namespace)

            # Check for deployment or statefulset
            resource = None
            replicas = 0
            ready_replicas = 0

            if resource_type == "statefulset":
                # Check StatefulSet
                statefulsets = self.apps_v1.list_namespaced_stateful_set(namespace)
                for s in statefulsets.items:
                    if s.metadata.name == deployment_name:
                        resource = s
                        replicas = s.spec.replicas or 0
                        ready_replicas = s.status.ready_replicas or 0
                        break
            else:
                # Check Deployment
                deployments = self.apps_v1.list_namespaced_deployment(namespace)
                for d in deployments.items:
                    if d.metadata.name == deployment_name:
                        resource = d
                        replicas = d.spec.replicas or 0
                        ready_replicas = d.status.ready_replicas or 0
                        break

            if resource:
                # Service is deployed
                return ServiceModel(
                    name=name,
                    display_name=service_config["display_name"],
                    description=service_config["description"],
                    type=service_type,
                    namespace=namespace,
                    category=service_config["category"],
                    icon=service_config["icon"],
                    url=self._construct_url(name, service_config.get("url_pattern")),
                    health_endpoint=service_config.get("health_endpoint"),
                    is_enabled=replicas > 0,
                    original_replicas=replicas or 1,
                    dependencies=service_config.get("dependencies", []),
                    service_metadata={
                        resource_type: resource.metadata.name,
                        "replicas": replicas,
                        "ready_replicas": ready_replicas,
                        "resource_type": resource_type,
                    },
                )

        except ApiException as e:
            if e.status != 404:  # Namespace or deployment not found is expected
                logger.error(
                    f"Error checking service {name} in namespace {namespace}: {e}"
                )

        return None

    def _construct_url(
        self, service_name: str, url_pattern: Optional[str] = None
    ) -> Optional[str]:
        """Construct service URL based on pattern and domain"""
        if url_pattern:
            return url_pattern.format(domain=self.domain)

        # Default pattern for user apps
        return f"https://{service_name}.{self.domain}"

    def sync_services(self, discovered: Dict[str, List[ServiceModel]]):
        """Sync discovered services with database"""
        all_discovered = []
        for service_list in discovered.values():
            all_discovered.extend(service_list)

        # Get existing services from database
        existing_services = {s.name: s for s in self.db.query(ServiceModel).all()}

        # Update or create services
        for service in all_discovered:
            if service.name in existing_services:
                # Update existing service
                existing = existing_services[service.name]
                existing.display_name = service.display_name
                existing.description = service.description
                existing.namespace = service.namespace
                existing.category = service.category
                existing.icon = service.icon
                existing.url = service.url
                existing.health_endpoint = service.health_endpoint
                existing.is_enabled = service.is_enabled
                existing.original_replicas = service.original_replicas
                existing.dependencies = service.dependencies
                existing.service_metadata = service.service_metadata
                existing.updated_at = datetime.utcnow()
            else:
                # Create new service
                self.db.add(service)

        # Mark services not found as disabled (for optional and user apps)
        discovered_names = {s.name for s in all_discovered}
        for name, service in existing_services.items():
            if name not in discovered_names and service.type != "core":
                service.is_enabled = False
                service.updated_at = datetime.utcnow()

        self.db.commit()


# 🤖 Generated with [Claude Code](https://claude.ai/code)
