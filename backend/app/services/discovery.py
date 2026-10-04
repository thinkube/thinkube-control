# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""ConfigMap-based service discovery"""

import base64
import logging
import yaml
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from sqlalchemy.orm import Session
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.models.services import Service as ServiceModel, ServiceEndpoint
from app.models.service_schemas import ServiceType
from sqlalchemy.dialects.postgresql import insert

logger = logging.getLogger(__name__)


# The Gitea organization that holds the repositories of deployed templates.
GITEA_ORG = "thinkube-deployments"


class ServiceDiscovery:
    """Discover services from Kubernetes ConfigMaps"""

    # Label to identify Thinkube-managed services
    SERVICE_LABEL = "thinkube.io/managed"
    _gitea_token: Optional[str] = None

    def _app_version(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        """An app's version and the reason it could not be read.

        An app's version changes with each push, while its service config is
        written only at deploy, so the version is read from metadata.version of
        thinkube.yaml on the app's Gitea main: the code the build deploys. The
        version is None when thinkube.yaml declares none. A failure is returned
        as the reason instead of raised, so the app still shows on the
        dashboard, with the reason in place of its version.
        """
        import urllib3
        from app.core.config import settings

        path = f"/repos/{GITEA_ORG}/{app_name}/raw/thinkube.yaml"
        try:
            if self._gitea_token is None:
                secret = self.core_v1.read_namespaced_secret("gitea-admin-token", "gitea")
                self._gitea_token = base64.b64decode(secret.data["token"]).decode()
            http = urllib3.PoolManager(cert_reqs="CERT_NONE")
            urllib3.disable_warnings()
            response = http.request(
                "GET",
                f"{settings.GITEA_URL}/api/v1{path}",
                fields={"ref": "main"},
                headers={"Authorization": f"token {self._gitea_token}"},
                timeout=10.0,
            )
            if response.status != 200:
                return None, f"Gitea answered {response.status} for {path}"
            config = yaml.safe_load(response.data) or {}
            return (config.get("metadata") or {}).get("version"), None
        except Exception as e:
            return None, f"Cannot read {path} from Gitea: {e}"

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

    def discover_all(self) -> Dict[str, List[ServiceModel]]:
        """Discover all services from ConfigMaps

        Returns:
            Dictionary with service types as keys and lists of services as values
        """
        services: Dict[str, List[ServiceModel]] = {
            "core": [],
            "optional": [],
            "user_app": [],
            "component": [],
        }

        try:
            # Get all ConfigMaps with our label
            configmaps = self.core_v1.list_config_map_for_all_namespaces(
                label_selector=f"{self.SERVICE_LABEL}=true"
            )

            for cm in configmaps.items:
                logger.info(
                    f"Processing ConfigMap {cm.metadata.name} in namespace {cm.metadata.namespace}"
                )
                service = self._extract_service_from_configmap(cm)
                if service:
                    # A type absent from the buckets above must not abort the
                    # whole sync; downstream consumers iterate the values.
                    services.setdefault(service.type, []).append(service)
                    logger.info(
                        f"Discovered service {service.name} of type {service.type} from ConfigMap"
                    )
                else:
                    logger.warning(
                        f"Failed to extract service from ConfigMap {cm.metadata.name}"
                    )

        except ApiException as e:
            logger.error(f"Failed to discover services from ConfigMaps: {e}")

        # Sync to database
        self._sync_services(services)

        return services

    def _extract_service_from_configmap(self, configmap: Any) -> Optional[ServiceModel]:
        """Extract service information from a ConfigMap"""
        try:
            # Get service.yaml content
            service_yaml = configmap.data.get("service.yaml")
            if not service_yaml:
                logger.warning(
                    f"ConfigMap {configmap.metadata.name} has no service.yaml"
                )
                return None

            # Parse YAML
            service_data = yaml.safe_load(service_yaml)
            if not service_data or "service" not in service_data:
                logger.warning(
                    f"Invalid service.yaml in ConfigMap {configmap.metadata.name}: {service_data}"
                )
                return None

            svc = service_data["service"]

            # Process domain placeholders
            processed_endpoints = []
            for endpoint in svc.get("endpoints", []):
                ep = endpoint.copy()
                if ep.get("url"):
                    ep["url"] = ep["url"].replace("{{ domain_name }}", self.domain)
                if ep.get("health_url"):
                    ep["health_url"] = ep["health_url"].replace("{{ domain_name }}", self.domain)
                processed_endpoints.append(ep)

            # Check if service is deployed
            scaling = svc.get("scaling", {})

            # Handle both old format (single resource) and new format (multiple resources)
            resources = scaling.get("resources", [])
            if not resources and scaling.get("resource_name"):
                # Old format - convert to new format
                resources = [
                    {
                        "resource_type": scaling.get("resource_type", "deployment"),
                        "resource_name": scaling.get("resource_name", svc["name"]),
                    }
                ]

            # Get deployment status if resources exist
            is_gateway_managed = scaling.get("gateway_managed", False)
            total_replicas = 0
            total_ready_replicas = 0

            for resource in resources:
                replicas, ready_replicas = self._get_resource_replicas(
                    resource.get("resource_type", "deployment"),
                    resource.get("resource_name"),
                    scaling.get("namespace", configmap.metadata.namespace),
                )
                total_replicas += replicas
                total_ready_replicas += ready_replicas

            if is_gateway_managed:
                gw_replicas, gw_ready = self._count_gateway_pods(
                    scaling.get("namespace", configmap.metadata.namespace)
                )
                total_replicas += gw_replicas
                total_ready_replicas += gw_ready

            # Use totals for multi-container apps
            replicas = total_replicas
            ready_replicas = total_ready_replicas

            # Gateway-managed services are always "enabled" even with 0 base replicas
            is_enabled = replicas > 0 or is_gateway_managed

            # A component's version is fixed at deploy; an app's follows its pushes.
            version_error = None
            if svc.get("type") == "user_app":
                component_version, version_error = self._app_version(svc["name"])
                if version_error:
                    logger.error(f"Version of {svc['name']}: {version_error}")
            else:
                component_version = svc.get("component_version")

            # Create service model
            service = ServiceModel(
                name=svc["name"],
                display_name=svc.get("display_name", svc["name"]),
                powered_by=svc.get("powered_by"),
                description=svc.get("description", ""),
                type=svc.get("type", "optional"),
                namespace=scaling.get("namespace", configmap.metadata.namespace),
                category=svc.get("category", "uncategorized"),
                icon=svc.get("icon", "mdi-server"),
                url=None,  # Will be set from primary endpoint
                health_endpoint=None,  # Will be set from primary endpoint
                is_enabled=is_enabled,
                original_replicas=replicas if replicas > 0 else (0 if is_gateway_managed else 1),
                dependencies=svc.get("dependencies", []),
                resource_type=(
                    resources[0].get("resource_type", "deployment")
                    if resources
                    else "deployment"
                ),
                resource_name=(
                    resources[0].get("resource_name", svc["name"])
                    if resources
                    else svc["name"]
                ),
                min_replicas=scaling.get("min_replicas", 0 if is_gateway_managed else 1),
                can_disable=scaling.get("can_disable", True),
                service_metadata={
                    "configmap": configmap.metadata.name,
                    "configmap_namespace": configmap.metadata.namespace,
                    "replicas": replicas,
                    "ready_replicas": ready_replicas,
                    "resources": resources,
                    "gateway_managed": is_gateway_managed,
                    # The component's VERSION, or an app's own version.
                    "component_version": component_version,
                    "version_error": version_error,
                    # For services deployed from a template: the template and
                    # the release tag the service was generated from.
                    "template_url": svc.get("metadata", {}).get("template_url"),
                    "template_version": svc.get("metadata", {}).get("template_version"),
                },
            )

            # Process endpoints
            for ep_data in processed_endpoints:
                endpoint = ServiceEndpoint(
                    name=ep_data["name"],
                    type=ep_data.get("type", "http"),
                    url=ep_data.get("url"),
                    port=ep_data.get("port"),
                    health_url=ep_data.get("health_url"),
                    health_service=ep_data.get("health_service"),
                    description=ep_data.get("description"),
                    is_primary=ep_data.get("primary", False),
                    is_internal=ep_data.get("internal", False),
                )
                service.endpoints.append(endpoint)

                # Set service URL and health endpoint from primary endpoint
                if endpoint.is_primary:
                    if endpoint.url:
                        service.url = endpoint.url
                    
                    # Use health_url for health endpoint (all ConfigMaps now use health_url)
                    if endpoint.health_url:
                        service.health_endpoint = endpoint.health_url

            return service

        except Exception as e:
            logger.error(
                f"Failed to extract service from ConfigMap {configmap.metadata.name}: {e}"
            )
            return None

    def _check_resource_exists(
        self, resource_type: str, resource_name: str, namespace: str
    ) -> bool:
        """Check if a Kubernetes resource exists"""
        try:
            if resource_type == "deployment":
                self.apps_v1.read_namespaced_deployment(resource_name, namespace)
                return True
            elif resource_type == "statefulset":
                self.apps_v1.read_namespaced_stateful_set(resource_name, namespace)
                return True
            elif resource_type == "daemonset":
                self.apps_v1.read_namespaced_daemon_set(resource_name, namespace)
                return True
        except ApiException as e:
            if e.status != 404:
                logger.error(
                    f"Error checking resource {resource_type}/{resource_name} in {namespace}: {e}"
                )
        return False

    def _get_resource_replicas(
        self, resource_type: str, resource_name: str, namespace: str
    ) -> tuple[int, int]:
        """Get replica count for a resource"""
        try:
            if resource_type == "deployment":
                deployment = self.apps_v1.read_namespaced_deployment(
                    resource_name, namespace
                )
                return (
                    deployment.spec.replicas or 0,
                    deployment.status.ready_replicas or 0,
                )
            elif resource_type == "statefulset":
                statefulset = self.apps_v1.read_namespaced_stateful_set(
                    resource_name, namespace
                )
                return (
                    statefulset.spec.replicas or 0,
                    statefulset.status.ready_replicas or 0,
                )
            elif resource_type == "daemonset":
                daemonset = self.apps_v1.read_namespaced_daemon_set(
                    resource_name, namespace
                )
                return (
                    daemonset.status.desired_number_scheduled or 0,
                    daemonset.status.number_ready or 0,
                )
        except ApiException as e:
            logger.error(
                f"Error getting replicas for {resource_type}/{resource_name} in {namespace}: {e}"
            )
        return 0, 0

    def _count_gateway_pods(self, namespace: str) -> tuple[int, int]:
        """Count gateway-managed pods in a namespace."""
        try:
            pods = self.core_v1.list_namespaced_pod(
                namespace,
                label_selector="thinkube.io/managed-by=llm-gateway",
            )
            total = 0
            ready = 0
            for pod in pods.items:
                if pod.status.phase in ("Running", "Pending"):
                    total += 1
                    conditions = pod.status.conditions or []
                    if any(c.type == "Ready" and c.status == "True" for c in conditions):
                        ready += 1
            return total, ready
        except Exception as e:
            logger.debug(f"Gateway pod count failed for {namespace}: {e}")
            return 0, 0

    def _sync_services(self, discovered: Dict[str, List[ServiceModel]]):
        """Sync discovered services with database using UPSERT pattern"""
        from sqlalchemy import text

        all_discovered = []
        seen_names = set()

        # First, deduplicate discovered services by name
        for service_list in discovered.values():
            for service in service_list:
                if service.name not in seen_names:
                    all_discovered.append(service)
                    seen_names.add(service.name)
                else:
                    logger.warning(
                        f"Duplicate service {service.name} found in discovery, skipping"
                    )

        # Get existing services for endpoint management
        existing_services = {s.name: s for s in self.db.query(ServiceModel).all()}

        # Process each service
        for service in all_discovered:
            try:
                # Prepare service data for upsert
                service_data = {
                    "name": service.name,
                    "display_name": service.display_name,
                    "powered_by": service.powered_by,
                    "description": service.description,
                    "type": service.type,
                    "namespace": service.namespace,
                    "category": service.category,
                    "icon": service.icon,
                    "url": service.url,
                    "health_endpoint": service.health_endpoint,
                    "is_enabled": service.is_enabled,
                    "original_replicas": service.original_replicas,
                    "dependencies": service.dependencies,
                    "resource_type": service.resource_type,
                    "resource_name": service.resource_name,
                    "min_replicas": service.min_replicas,
                    "can_disable": service.can_disable,
                    "service_metadata": service.service_metadata,
                    "updated_at": datetime.utcnow(),
                }

                # Use PostgreSQL UPSERT
                stmt = insert(ServiceModel).values(**service_data)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["name"],
                    set_={
                        "display_name": stmt.excluded.display_name,
                        "powered_by": stmt.excluded.powered_by,
                        "description": stmt.excluded.description,
                        "type": stmt.excluded.type,  # THIS WAS MISSING - must update type field!
                        "namespace": stmt.excluded.namespace,
                        "category": stmt.excluded.category,
                        "icon": stmt.excluded.icon,
                        "url": stmt.excluded.url,
                        "health_endpoint": stmt.excluded.health_endpoint,
                        "is_enabled": stmt.excluded.is_enabled,
                        "original_replicas": stmt.excluded.original_replicas,
                        "dependencies": stmt.excluded.dependencies,
                        "resource_type": stmt.excluded.resource_type,
                        "resource_name": stmt.excluded.resource_name,
                        "min_replicas": stmt.excluded.min_replicas,
                        "can_disable": stmt.excluded.can_disable,
                        "service_metadata": stmt.excluded.service_metadata,
                        "updated_at": stmt.excluded.updated_at,
                    },
                ).returning(ServiceModel.id)

                result = self.db.execute(stmt)
                service_id = result.scalar()

                # Handle endpoints separately
                if service.name in existing_services:
                    # Delete old endpoints
                    self.db.execute(
                        text(
                            "DELETE FROM service_endpoints WHERE service_id = :service_id"
                        ),
                        {"service_id": service_id},
                    )

                # Add new endpoints using raw insert to avoid cascade
                for endpoint in service.endpoints:
                    endpoint_stmt = insert(ServiceEndpoint).values(
                        service_id=service_id,
                        name=endpoint.name,
                        type=endpoint.type,
                        url=endpoint.url,
                        port=endpoint.port,
                        health_url=endpoint.health_url,
                        health_service=endpoint.health_service,
                        description=endpoint.description,
                        is_primary=endpoint.is_primary,
                        is_internal=endpoint.is_internal,
                    )
                    self.db.execute(endpoint_stmt)

                logger.info(f"Upserted service {service.name}")

            except Exception as e:
                logger.error(f"Failed to upsert service {service.name}: {e}")
                # Continue with other services

        # Mark services not found as disabled
        discovered_names = {s.name for s in all_discovered}
        try:
            self.db.execute(
                text(
                    """
                    UPDATE services 
                    SET is_enabled = false, updated_at = :now 
                    WHERE name NOT IN :names AND is_enabled = true
                """
                ),
                {
                    "names": tuple(discovered_names) if discovered_names else ("",),
                    "now": datetime.utcnow(),
                },
            )
        except Exception as e:
            logger.error(f"Failed to mark missing services as disabled: {e}")

        # Commit all changes in one transaction
        try:
            self.db.commit()
            logger.info(
                f"Discovery completed. Processed {len(all_discovered)} services"
            )
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to commit service discovery changes: {e}")
            raise


# 🤖 Generated with [Claude Code](https://claude.ai/code)
