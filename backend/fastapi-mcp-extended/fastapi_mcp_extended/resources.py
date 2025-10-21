"""Resource handling for MCP protocol."""

import json
import logging
import re
from typing import Dict, List, Any, Optional
import mcp.types as types

logger = logging.getLogger(__name__)


class ResourceHandler:
    """Handles resource conversion and management."""

    def __init__(self, operation_map: Dict[str, Dict], base_url: str = "resource://"):
        """
        Initialize resource handler.

        Args:
            operation_map: Mapping of operation IDs to operation details
            base_url: Base URL for resource URIs
        """
        self.operation_map = operation_map
        self.base_url = base_url
        self.resources: List[types.Resource] = []
        self.resource_templates: List[types.ResourceTemplate] = []
        self._resource_cache: Dict[str, Any] = {}

    def path_to_uri(self, path: str) -> str:
        """
        Convert an API path to a resource URI.

        Examples:
            /api/services -> resource://services
            /api/v1/items -> resource://items
            /harbor/images -> resource://harbor/images
        """
        # Remove leading slash and API prefixes
        clean_path = path.lstrip("/")
        clean_path = re.sub(r"^api/v?\d*/", "", clean_path)

        # Convert to resource URI - don't use urljoin as it doesn't work with resource://
        if not clean_path:
            clean_path = "root"
        return f"{self.base_url}{clean_path}"

    def create_resource(self, operation_id: str, operation: Dict) -> types.Resource:
        """
        Create a Resource from an operation.

        Args:
            operation_id: The operation ID
            operation: Operation details from OpenAPI

        Returns:
            MCP Resource object
        """
        # Use operation_id as the URI to avoid duplicates and be consistent with tools
        # This makes resources accessible like: resource://get_user_info
        uri = f"{self.base_url}{operation_id}"

        # Extract description from operation
        description = operation.get("summary", "")
        if not description and "description" in operation:
            description = operation["description"][:100]  # Truncate long descriptions

        return types.Resource(
            uri=uri,
            name=operation_id,
            description=description or f"Resource for {operation_id}",
            mimeType="application/json",  # Default to JSON
        )

    def create_resource_template(
        self, operation_id: str, operation: Dict
    ) -> types.ResourceTemplate:
        """
        Create a ResourceTemplate for parameterized endpoints.

        Args:
            operation_id: The operation ID
            operation: Operation details from OpenAPI

        Returns:
            MCP ResourceTemplate object
        """
        path = operation.get("path", "")

        # Convert path parameters from {id} to URI template format
        uri_template = self.path_to_uri(path)

        # Extract description
        description = operation.get("summary", "")
        if not description and "description" in operation:
            description = operation["description"][:100]

        return types.ResourceTemplate(
            uriTemplate=uri_template,
            name=operation_id,
            description=description or f"Resource template for {operation_id}",
            mimeType="application/json",
        )

    def build_resources(
        self, resource_ops: List[str], resource_template_ops: List[str]
    ) -> None:
        """
        Build resource and resource template lists from operation IDs.

        Args:
            resource_ops: List of operation IDs for resources
            resource_template_ops: List of operation IDs for resource templates
        """
        # Create resources
        for op_id in resource_ops:
            if op_id in self.operation_map:
                resource = self.create_resource(op_id, self.operation_map[op_id])
                self.resources.append(resource)

        # Create resource templates
        for op_id in resource_template_ops:
            if op_id in self.operation_map:
                template = self.create_resource_template(op_id, self.operation_map[op_id])
                self.resource_templates.append(template)

    async def read_resource(
        self, uri: str, api_executor, client, http_request_info: Optional[Any] = None
    ) -> List[types.TextResourceContents]:
        """
        Read a resource by executing the corresponding API call.

        Args:
            uri: Resource URI to read
            api_executor: Function to execute API calls
            client: HTTP client to use for API calls
            http_request_info: Optional HTTP request context

        Returns:
            Resource content as TextResourceContents
        """
        # Convert URI to string if it's a URL object
        uri_str = str(uri)

        # Check cache first
        if uri_str in self._resource_cache:
            cached = self._resource_cache[uri_str]
            return [types.TextResourceContents(
                uri=uri_str,
                mimeType="application/json",
                text=cached if isinstance(cached, str) else json.dumps(cached)
            )]

        # Check if the URI is actually an operation_id (for simpler access)
        # This allows using resource://get_user_info instead of resource://auth/userinfo
        matching_op = None

        # First, try to match by operation_id directly if it looks like one
        if uri_str.startswith(self.base_url):
            potential_op_id = uri_str[len(self.base_url):]
            if potential_op_id in self.operation_map:
                matching_op = potential_op_id
                logger.debug(f"Found direct operation_id match: {matching_op}")

        # If not found by operation_id, try path matching
        if not matching_op:
            # Parse URI to find matching operation by path
            if uri_str.startswith(self.base_url):
                path = uri_str[len(self.base_url):]
            else:
                path = uri_str

            # Ensure path starts with /
            if not path.startswith("/"):
                path = "/" + path

            # Add /api/v1 prefix if not present (since resources strip this but API paths have it)
            if not path.startswith("/api"):
                path = f"/api/v1{path}"

            logger.debug(f"Looking for path: {path} in operation map")

            # Find matching operation by path
            for op_id, op_info in self.operation_map.items():
                op_path = op_info.get("path", "")
                if self._paths_match(path, op_path):
                    matching_op = op_id
                    logger.debug(f"Found match by path: {op_id} for path {path}")
                    break

        if not matching_op:
            logger.error(f"No matching operation for URI: {uri_str}")
            return [
                types.TextResourceContents(
                    uri=uri_str,
                    mimeType="text/plain",
                    text=f"Resource not found: {uri_str}",
                )
            ]

        # Execute the API call
        # Extract any path parameters from the URI
        # Use the operation's actual path for parameter extraction
        op_path = self.operation_map[matching_op]["path"]
        # If we matched by operation_id, we don't have path params from the URI
        # If we matched by path, extract params from the matched path
        if 'path' in locals():
            arguments = self._extract_path_params(path, op_path)
        else:
            arguments = {}

        # The API executor (_execute_api_tool) expects client as first parameter
        logger.debug(f"Calling API executor for {matching_op} with args: {arguments}")

        result = await api_executor(
            client=client,
            tool_name=matching_op,
            arguments=arguments,
            operation_map=self.operation_map,
            http_request_info=http_request_info,
        )

        logger.debug(f"API executor returned type: {type(result)}, value: {result}")

        # The API executor returns a list of TextContent objects
        # Extract the text from the first TextContent
        if result and len(result) > 0:
            # result[0] is a types.TextContent with a 'text' attribute
            if hasattr(result[0], 'text'):
                text_content = result[0].text
            else:
                logger.error(f"Result[0] has no 'text' attribute. Type: {type(result[0])}, Dir: {dir(result[0])}")
                text_content = str(result[0])
        else:
            text_content = "{}"

        # Cache the raw content
        self._resource_cache[uri_str] = text_content

        # Return as TextResourceContents
        return [
            types.TextResourceContents(
                uri=uri_str,
                mimeType="application/json",
                text=text_content
            )
        ]

    def _paths_match(self, actual_path: str, template_path: str) -> bool:
        """Check if an actual path matches a template path with parameters."""
        # Convert template path to regex
        # Replace {param} with regex pattern
        pattern = re.sub(r"\{[^}]+\}", r"[^/]+", template_path)
        pattern = f"^{pattern}$"
        return bool(re.match(pattern, actual_path))

    def _extract_path_params(self, actual_path: str, template_path: str) -> Dict[str, str]:
        """Extract path parameters from an actual path using a template."""
        params = {}

        # Split paths into segments
        actual_segments = actual_path.strip("/").split("/")
        template_segments = template_path.strip("/").split("/")

        if len(actual_segments) != len(template_segments):
            return params

        for actual, template in zip(actual_segments, template_segments):
            if template.startswith("{") and template.endswith("}"):
                param_name = template[1:-1]
                params[param_name] = actual

        return params