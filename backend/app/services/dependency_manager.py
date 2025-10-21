"""Service dependency management"""

import logging
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session

from app.models.services import Service as ServiceModel
from app.models.service_schemas import ServiceType


logger = logging.getLogger(__name__)


class DependencyManager:
    """Handle service dependencies"""

    def __init__(self, db: Session):
        """Initialize dependency manager

        Args:
            db: Database session
        """
        self.db = db

    def check_dependencies(self, service: ServiceModel) -> List[Dict[str, Any]]:
        """Check if all dependencies are enabled

        Args:
            service: ServiceModel to check dependencies for

        Returns:
            List of dependency status dictionaries
        """
        dependency_status = []

        for dep_name in service.dependencies:
            dep_service = (
                self.db.query(ServiceModel)
                .filter(ServiceModel.name == dep_name)
                .first()
            )

            if dep_service:
                dependency_status.append(
                    {
                        "name": dep_name,
                        "display_name": dep_service.display_name,
                        "exists": True,
                        "enabled": dep_service.is_enabled,
                        "type": dep_service.type,
                        "health": (
                            dep_service.latest_health.status
                            if dep_service.latest_health
                            else "unknown"
                        ),
                    }
                )
            else:
                dependency_status.append(
                    {
                        "name": dep_name,
                        "display_name": dep_name,
                        "exists": False,
                        "enabled": False,
                        "type": None,
                        "health": "unknown",
                    }
                )

        return dependency_status

    def get_dependents(self, service: ServiceModel) -> List[ServiceModel]:
        """Get services that depend on this service

        Args:
            service: ServiceModel to find dependents for

        Returns:
            List of dependent services
        """
        # For PostgreSQL JSON columns, we need to use the @> operator
        # But since SQLAlchemy's JSON support varies, let's fetch all and filter in Python
        all_services = self.db.query(ServiceModel).all()
        dependents = []
        
        for svc in all_services:
            if svc.dependencies and service.name in svc.dependencies:
                dependents.append(svc)
        
        return dependents

    def validate_enable_action(
        self, service: ServiceModel
    ) -> Tuple[bool, Optional[str], List[str]]:
        """Check if service can be enabled

        Args:
            service: ServiceModel to enable

        Returns:
            Tuple of (can_enable, error_message, disabled_dependencies)
        """
        # Core services are always enabled
        if service.type == "core":
            return False, "Core services cannot be disabled", []

        # Check if all dependencies are enabled
        dependency_status = self.check_dependencies(service)
        disabled_deps = [
            dep["display_name"] for dep in dependency_status if not dep["enabled"]
        ]

        if disabled_deps:
            return (
                False,
                f"Cannot enable {service.display_name} because the following dependencies are disabled: {', '.join(disabled_deps)}",
                disabled_deps,
            )

        return True, None, []

    def validate_disable_action(
        self, service: ServiceModel
    ) -> Tuple[bool, Optional[str], List[str]]:
        """Check if service can be safely disabled

        Args:
            service: ServiceModel to disable

        Returns:
            Tuple of (can_disable, warning_message, affected_services)
        """
        # Core services cannot be disabled
        if service.type == "core":
            return False, "Core services cannot be disabled", []

        # Find dependent services
        dependents = self.get_dependents(service)
        enabled_dependents = [dep for dep in dependents if dep.is_enabled]

        if enabled_dependents:
            affected_names = [dep.display_name for dep in enabled_dependents]
            warning = f"Disabling {service.display_name} will affect the following services: {', '.join(affected_names)}"
            return True, warning, affected_names

        return True, None, []

    def get_dependency_tree(
        self, service: ServiceModel, visited: Optional[set] = None
    ) -> Dict[str, Any]:
        """Get the full dependency tree for a service

        Args:
            service: ServiceModel to get dependency tree for
            visited: Set of visited service names to prevent cycles

        Returns:
            Dictionary representing the dependency tree
        """
        if visited is None:
            visited = set()

        if service.name in visited:
            # Circular dependency detected
            return {
                "name": service.name,
                "display_name": service.display_name,
                "type": service.type,
                "enabled": service.is_enabled,
                "circular": True,
                "dependencies": [],
            }

        visited.add(service.name)

        tree = {
            "name": service.name,
            "display_name": service.display_name,
            "type": service.type,
            "enabled": service.is_enabled,
            "health": (
                service.latest_health.status if service.latest_health else "unknown"
            ),
            "dependencies": [],
        }

        # Get dependencies recursively
        for dep_name in service.dependencies:
            dep_service = (
                self.db.query(ServiceModel)
                .filter(ServiceModel.name == dep_name)
                .first()
            )

            if dep_service:
                dep_tree = self.get_dependency_tree(dep_service, visited)
                tree["dependencies"].append(dep_tree)
            else:
                # Dependency not found
                tree["dependencies"].append(
                    {
                        "name": dep_name,
                        "display_name": dep_name,
                        "type": None,
                        "enabled": False,
                        "health": "unknown",
                        "missing": True,
                        "dependencies": [],
                    }
                )

        return tree

    def get_enable_order(self, service: ServiceModel) -> List[ServiceModel]:
        """Get the order in which services should be enabled

        Args:
            service: Target service to enable

        Returns:
            List of services in the order they should be enabled
        """
        enable_order = []
        visited = set()

        def add_dependencies(svc: Service):
            if svc.name in visited:
                return

            visited.add(svc.name)

            # Add dependencies first
            for dep_name in svc.dependencies:
                dep_service = (
                    self.db.query(ServiceModel)
                    .filter(ServiceModel.name == dep_name)
                    .first()
                )
                if dep_service and not dep_service.is_enabled:
                    add_dependencies(dep_service)

            # Then add the service itself
            if not svc.is_enabled and svc not in enable_order:
                enable_order.append(svc)

        add_dependencies(service)
        return enable_order

    def get_disable_order(self, service: ServiceModel) -> List[ServiceModel]:
        """Get the order in which services should be disabled

        Args:
            service: Target service to disable

        Returns:
            List of services in the order they should be disabled
        """
        disable_order = []

        # First add all dependents
        def add_dependents(svc: Service):
            dependents = self.get_dependents(svc)
            for dep in dependents:
                if dep.is_enabled and dep not in disable_order:
                    add_dependents(dep)  # Recursively add their dependents
                    disable_order.append(dep)

        add_dependents(service)

        # Finally add the service itself
        if service not in disable_order:
            disable_order.append(service)

        return disable_order

    def validate_service_name(
        self, name: str, service_type: ServiceType
    ) -> Tuple[bool, Optional[str]]:
        """Validate if a service name can be used

        Args:
            name: Proposed service name
            service_type: Type of service being created

        Returns:
            Tuple of (is_valid, error_message)
        """
        from app.models.service_schemas import ServiceValidation

        # Check if it's a reserved name
        if ServiceValidation.is_reserved_name(name):
            reserved_type = ServiceValidation.get_reserved_type(name)
            return False, f"Name '{name}' is reserved for {reserved_type} services"

        # Check if name already exists
        existing = self.db.query(ServiceModel).filter(ServiceModel.name == name).first()
        if existing:
            if existing.type == "user_app" and service_type == "user_app":
                # User app with same name exists, overwrite is possible
                return (
                    True,
                    f"A user application named '{name}' already exists and will be overwritten",
                )
            else:
                return (
                    False,
                    f"Name '{name}' is already in use by a {existing.type} service",
                )

        return True, None


# 🤖 Generated with [Claude Code](https://claude.ai/code)
