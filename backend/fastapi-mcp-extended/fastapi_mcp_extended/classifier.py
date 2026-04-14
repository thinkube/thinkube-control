"""Smart endpoint classification for resources vs tools."""

import re
from typing import Dict, List, Optional, Tuple
from enum import Enum


class EndpointType(Enum):
    """Classification of endpoint types."""

    RESOURCE = "resource"
    RESOURCE_TEMPLATE = "resource_template"
    TOOL = "tool"


class EndpointClassifier:
    """Classifies FastAPI endpoints as resources or tools."""

    # Default patterns for resources (read-only operations)
    DEFAULT_RESOURCE_PATTERNS = [
        r"^/.*/(list|get|status|info|stats|health)/?$",
        r"^/.*/\{[^}]+\}/?$",  # GET with path parameter
        r"^/.*/(dashboards?|services?|templates?|deployments?|components?|images?)/?$",
    ]

    # Default patterns for tools (operations with side effects)
    DEFAULT_TOOL_PATTERNS = [
        r"^/.*/(create|add|update|modify|delete|remove|restart|toggle|install|uninstall|deploy|register|mirror)/?$",
        r"^/.*/remirror/?$",
        r"^/.*/sync/?$",
        r"^/.*/build/?$",
    ]

    # Known operation IDs that should be tools despite being GETs
    TOOL_OPERATION_IDS = {
        "restart_service",
        "toggle_service",
        "remirror_harbor_image",
        "bulk_mirror_images",
    }

    # Known operation IDs that should be resources
    RESOURCE_OPERATION_IDS = {
        # Auth
        "get_user_info",
        "list_tokens",
        "verify_current_token",
        # Services
        "list_services_minimal",
        "get_service_details",
        "get_service_health_history",
        "get_service_dependencies",
        "describe_pod",
        "get_container_logs",
        # Dashboards
        "list_dashboards",
        "get_dashboard_categories",
        "get_dashboard",
        # Templates
        "list_templates",
        "get_template_metadata",
        "list_deployments",
        "get_deployment_status",
        "get_deployment_logs",
        "get_deployment_debug_logs",
        "download_debug_log",
        # Harbor images
        "list_harbor_images",
        "get_harbor_image",
        "get_image_statistics",
        "list_harbor_jobs",
        "get_harbor_job_status",
        "list_harbor_projects",
        "check_harbor_health",
        # Secrets
        "list_secrets",
        "get_secret",
        "get_secret_apps",
        # Custom images
        "list_custom_images",
        "get_custom_image",
        "get_base_registry",
        "get_image_dockerfile",
        "get_build_logs",
        "download_build_log",
        # Models
        "get_model_catalog",
        "list_mirror_jobs",
        "get_mirror_status",
        "check_mlflow_status",
        # Jupyter venvs
        "list_jupyter_venvs",
        "get_jupyter_venv",
        "get_venv_templates",
        "get_venv_template_details",
        "get_venv_build_logs",
        "download_venv_build_log",
        # JupyterHub
        "get_jupyterhub_config",
        # Optional components
        "list_optional_components",
        "get_component_info",
        "get_component_status",
        # Knative
        "list_knative_services",
        "get_knative_service",
        # Cluster
        "get_cluster_resources",
        "get_gpu_metrics",
        # CI/CD
        "list_pipelines",
        "get_pipeline",
        "get_metrics",
        "list_applications",
        # Debug
        "resolve_hostname",
        "test_connectivity",
        "get_environment",
        "test_ssh",
    }

    def __init__(
        self,
        resource_patterns: Optional[List[str]] = None,
        tool_patterns: Optional[List[str]] = None,
        auto_convert_gets: bool = True,
    ):
        """Initialize the classifier with custom patterns."""
        self.auto_convert_gets = auto_convert_gets

        # Compile regex patterns
        self.resource_patterns = [
            re.compile(p) for p in (resource_patterns or self.DEFAULT_RESOURCE_PATTERNS)
        ]
        self.tool_patterns = [
            re.compile(p) for p in (tool_patterns or self.DEFAULT_TOOL_PATTERNS)
        ]

    def classify(
        self,
        path: str,
        method: str,
        operation_id: Optional[str] = None,
    ) -> EndpointType:
        """
        Classify an endpoint as resource or tool.

        Args:
            path: The endpoint path (e.g., /api/services)
            method: HTTP method (GET, POST, etc.)
            operation_id: Optional operation ID from OpenAPI

        Returns:
            EndpointType classification
        """
        # Check operation ID overrides first
        if operation_id:
            if operation_id in self.TOOL_OPERATION_IDS:
                return EndpointType.TOOL
            if operation_id in self.RESOURCE_OPERATION_IDS:
                if "{" in path:
                    return EndpointType.RESOURCE_TEMPLATE
                return EndpointType.RESOURCE

        # Check explicit tool patterns
        for pattern in self.tool_patterns:
            if pattern.match(path):
                return EndpointType.TOOL

        # Check explicit resource patterns
        for pattern in self.resource_patterns:
            if pattern.match(path):
                if "{" in path:
                    return EndpointType.RESOURCE_TEMPLATE
                return EndpointType.RESOURCE

        # Default classification by method
        if method == "GET" and self.auto_convert_gets:
            # GET endpoints default to resources
            if "{" in path:
                return EndpointType.RESOURCE_TEMPLATE
            return EndpointType.RESOURCE
        else:
            # POST, PUT, DELETE, PATCH default to tools
            return EndpointType.TOOL

    def classify_operations(
        self, operations: Dict[str, Dict]
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Classify a dictionary of operations from OpenAPI.

        Args:
            operations: Dictionary of operation_id -> operation info

        Returns:
            Tuple of (resource_ops, resource_template_ops, tool_ops)
        """
        resources = []
        resource_templates = []
        tools = []

        for op_id, op_info in operations.items():
            path = op_info.get("path", "")
            method = op_info.get("method", "GET")

            endpoint_type = self.classify(path, method, op_id)

            if endpoint_type == EndpointType.RESOURCE:
                resources.append(op_id)
            elif endpoint_type == EndpointType.RESOURCE_TEMPLATE:
                resource_templates.append(op_id)
            else:
                tools.append(op_id)

        return resources, resource_templates, tools