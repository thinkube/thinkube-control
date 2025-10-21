"""Service discovery v2 - Dynamic discovery based on Kubernetes annotations"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.models.services import Service as ServiceModel
from app.models.service_schemas import ServiceType

logger = logging.getLogger(__name__)


class DynamicServiceDiscovery:
    """Discover services dynamically based on Kubernetes annotations"""

    # Annotation keys that services should use
    ANNOTATION_PREFIX = "thinkube.io/"
    ANNOTATIONS = {
        "enabled": f"{ANNOTATION_PREFIX}enabled",  # "true"/"false"
        "display-name": f"{ANNOTATION_PREFIX}display-name",
        "description": f"{ANNOTATION_PREFIX}description",
        "category": f"{ANNOTATION_PREFIX}category",
        "icon": f"{ANNOTATION_PREFIX}icon",
        "health-endpoint": f"{ANNOTATION_PREFIX}health-endpoint",
        "health-path": f"{ANNOTATION_PREFIX}health-path",  # Just the path, not full URL
        "type": f"{ANNOTATION_PREFIX}type",  # core/optional/user_app
        "dependencies": f"{ANNOTATION_PREFIX}dependencies",  # comma-separated
        "url-pattern": f"{ANNOTATION_PREFIX}url-pattern",  # e.g., "https://{name}.{domain}"
    }

    # Label to identify Thinkube-managed services
    SERVICE_LABEL = "thinkube.io/managed"

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
        self.networking_v1 = client.NetworkingV1Api()

    def discover_all(self) -> Dict[str, List[ServiceModel]]:
        """Discover all services in the cluster

        Returns:
            Dictionary with service types as keys and lists of services as values
        """
        services = {"core": [], "optional": [], "user_app": []}

        # Discover from Deployments
        services_from_deployments = self._discover_from_deployments()
        for svc in services_from_deployments:
            services[svc.type].append(svc)

        # Discover from StatefulSets
        services_from_statefulsets = self._discover_from_statefulsets()
        for svc in services_from_statefulsets:
            services[svc.type].append(svc)

        # Discover from Ingresses (for services that only have ingress)
        services_from_ingresses = self._discover_from_ingresses()
        for svc in services_from_ingresses:
            services[svc.type].append(svc)

        # Sync to database
        self._sync_services(services)

        return services

    def _discover_from_deployments(self) -> List[ServiceModel]:
        """Discover services from Deployments"""
        discovered = []

        try:
            deployments = self.apps_v1.list_deployment_for_all_namespaces(
                label_selector=self.SERVICE_LABEL
            )

            for deployment in deployments.items:
                service = self._extract_service_from_resource(
                    deployment,
                    "deployment",
                    deployment.spec.replicas or 0,
                    deployment.status.ready_replicas or 0,
                )
                if service:
                    discovered.append(service)

        except ApiException as e:
            logger.error(f"Failed to discover services from deployments: {e}")

        return discovered

    def _discover_from_statefulsets(self) -> List[ServiceModel]:
        """Discover services from StatefulSets"""
        discovered = []

        try:
            statefulsets = self.apps_v1.list_stateful_set_for_all_namespaces(
                label_selector=self.SERVICE_LABEL
            )

            for statefulset in statefulsets.items:
                service = self._extract_service_from_resource(
                    statefulset,
                    "statefulset",
                    statefulset.spec.replicas or 0,
                    statefulset.status.ready_replicas or 0,
                )
                if service:
                    discovered.append(service)

        except ApiException as e:
            logger.error(f"Failed to discover services from statefulsets: {e}")

        return discovered

    def _discover_from_ingresses(self) -> List[ServiceModel]:
        """Discover services from Ingresses (for edge cases)"""
        discovered = []
        discovered_names = set()

        # Get already discovered service names to avoid duplicates
        for svc_list in [
            self._discover_from_deployments(),
            self._discover_from_statefulsets(),
        ]:
            for svc in svc_list:
                discovered_names.add(svc.name)

        try:
            ingresses = self.networking_v1.list_ingress_for_all_namespaces(
                label_selector=self.SERVICE_LABEL
            )

            for ingress in ingresses.items:
                name = ingress.metadata.name
                if name in discovered_names:
                    continue

                service = self._extract_service_from_ingress(ingress)
                if service:
                    discovered.append(service)

        except ApiException as e:
            logger.error(f"Failed to discover services from ingresses: {e}")

        return discovered

    def _extract_service_from_resource(
        self, resource: Any, resource_type: str, replicas: int, ready_replicas: int
    ) -> Optional[ServiceModel]:
        """Extract service information from a Kubernetes resource"""
        metadata = resource.metadata
        annotations = metadata.annotations or {}

        # Skip if not enabled
        if annotations.get(self.ANNOTATIONS["enabled"], "true").lower() == "false":
            return None

        # Extract service information
        name = metadata.name
        namespace = metadata.namespace
        service_type = annotations.get(self.ANNOTATIONS["type"], "optional")

        # Build URL
        url_pattern = annotations.get(self.ANNOTATIONS["url-pattern"])
        if url_pattern:
            url = url_pattern.format(name=name, namespace=namespace, domain=self.domain)
        else:
            # Try to find from ingress
            url = self._find_url_from_ingress(name, namespace)

        # Get health endpoint
        health_endpoint = annotations.get(self.ANNOTATIONS["health-endpoint"])
        if not health_endpoint:
            # Build from path if provided
            health_path = annotations.get(self.ANNOTATIONS["health-path"], "/health")
            if url:
                health_endpoint = f"{url.rstrip('/')}{health_path}"

        # Parse dependencies
        deps_str = annotations.get(self.ANNOTATIONS["dependencies"], "")
        dependencies = [d.strip() for d in deps_str.split(",") if d.strip()]

        return ServiceModel(
            name=name,
            display_name=annotations.get(self.ANNOTATIONS["display-name"], name),
            description=annotations.get(
                self.ANNOTATIONS["description"], f"{name} service"
            ),
            type=service_type,
            namespace=namespace,
            category=annotations.get(self.ANNOTATIONS["category"], "uncategorized"),
            icon=annotations.get(self.ANNOTATIONS["icon"], "mdi-server"),
            url=url,
            health_endpoint=health_endpoint,
            is_enabled=replicas > 0,
            original_replicas=replicas or 1,
            dependencies=dependencies,
            service_metadata={
                "resource_type": resource_type,
                "resource_name": name,
                "replicas": replicas,
                "ready_replicas": ready_replicas,
                "annotations": annotations,
            },
        )

    def _extract_service_from_ingress(self, ingress: Any) -> Optional[ServiceModel]:
        """Extract service information from an Ingress (fallback)"""
        metadata = ingress.metadata
        annotations = metadata.annotations or {}

        # Skip if not enabled
        if annotations.get(self.ANNOTATIONS["enabled"], "true").lower() == "false":
            return None

        name = metadata.name
        namespace = metadata.namespace

        # Get URL from ingress
        url = None
        if ingress.spec.rules:
            host = ingress.spec.rules[0].host
            tls = bool(ingress.spec.tls)
            protocol = "https" if tls else "http"
            url = f"{protocol}://{host}"

        return ServiceModel(
            name=name,
            display_name=annotations.get(self.ANNOTATIONS["display-name"], name),
            description=annotations.get(
                self.ANNOTATIONS["description"], f"{name} service"
            ),
            type=annotations.get(self.ANNOTATIONS["type"], "optional"),
            namespace=namespace,
            category=annotations.get(self.ANNOTATIONS["category"], "uncategorized"),
            icon=annotations.get(self.ANNOTATIONS["icon"], "mdi-server"),
            url=url,
            health_endpoint=annotations.get(self.ANNOTATIONS["health-endpoint"]),
            is_enabled=True,  # Assume enabled if has ingress
            original_replicas=1,
            dependencies=[],
            service_metadata={
                "resource_type": "ingress",
                "discovered_from": "ingress_only",
            },
        )

    def _find_url_from_ingress(self, name: str, namespace: str) -> Optional[str]:
        """Find URL for a service from its ingress"""
        try:
            ingresses = self.networking_v1.list_namespaced_ingress(namespace)
            for ingress in ingresses.items:
                # Check if this ingress is for our service
                if ingress.metadata.name == name or name in ingress.metadata.name:
                    if ingress.spec.rules:
                        host = ingress.spec.rules[0].host
                        tls = bool(ingress.spec.tls)
                        protocol = "https" if tls else "http"
                        return f"{protocol}://{host}"
        except ApiException:
            pass
        return None

    def _sync_services(self, discovered: Dict[str, List[ServiceModel]]):
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

        # Mark services not found as disabled
        discovered_names = {s.name for s in all_discovered}
        for name, service in existing_services.items():
            if name not in discovered_names:
                service.is_enabled = False
                service.updated_at = datetime.utcnow()
                logger.info(f"Service {name} not found in cluster, marking as disabled")

        self.db.commit()
        logger.info(f"Synced {len(all_discovered)} services to database")


# 🤖 Generated with [Claude Code](https://claude.ai/code)
