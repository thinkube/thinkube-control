"""Extended FastAPI MCP server with resource and prompt support."""

import logging
from typing import Dict, Optional, List, Any, Union
from fastapi import FastAPI, APIRouter
from fastapi_mcp import FastApiMCP
import mcp.types as types

from .classifier import EndpointClassifier
from .resources import ResourceHandler
from .prompts import PromptHandler
from .types import ResourceMapping, PromptDefinition

logger = logging.getLogger(__name__)


class ExtendedFastApiMCP(FastApiMCP):
    """Extended FastAPI MCP server with resource and prompt support."""

    def __init__(
        self,
        fastapi: FastAPI,
        *args,
        # Resource configuration
        resource_mapping: Optional[ResourceMapping] = None,
        auto_convert_resources: bool = True,
        dual_exposure: bool = True,  # Expose GET endpoints as both tools and resources
        resource_patterns: Optional[List[str]] = None,
        tool_patterns: Optional[List[str]] = None,
        # Prompt configuration
        prompt_definitions: Optional[List[PromptDefinition]] = None,
        add_default_prompts: bool = True,
        # Pass through to parent
        **kwargs,
    ):
        """
        Initialize extended MCP server.

        Args:
            fastapi: FastAPI application
            resource_mapping: Resource mapping configuration
            auto_convert_resources: Auto-convert GET endpoints to resources
            dual_exposure: Expose GET endpoints as both tools and resources
            resource_patterns: Regex patterns for resource endpoints
            tool_patterns: Regex patterns for tool endpoints
            prompt_definitions: List of prompt definitions
            add_default_prompts: Whether to add default prompts
            *args, **kwargs: Passed to parent FastApiMCP
        """
        # Store patterns BEFORE calling parent init (which calls setup_server)
        self._resource_patterns = resource_patterns
        self._tool_patterns = tool_patterns
        self._add_default_prompts = add_default_prompts
        self._prompt_definitions = prompt_definitions
        self.resource_mapping = resource_mapping or ResourceMapping()
        self.auto_convert_resources = auto_convert_resources
        self.dual_exposure = dual_exposure

        # Initialize parent (this will call setup_server)
        super().__init__(fastapi, *args, **kwargs)

    def setup_server(self) -> None:
        """Set up the MCP server with resources and prompts."""
        # Call parent setup to get tools
        super().setup_server()

        # Initialize handlers now that operation_map exists
        self.classifier = EndpointClassifier(
            resource_patterns=self._resource_patterns,
            tool_patterns=self._tool_patterns,
            auto_convert_gets=self.auto_convert_resources,
        )
        self.resource_handler = ResourceHandler(self.operation_map)
        self.prompt_handler = PromptHandler()

        # Add prompts
        if self._add_default_prompts:
            self.prompt_handler.add_default_prompts()
        if self._prompt_definitions:
            for prompt_def in self._prompt_definitions:
                self.prompt_handler.add_prompt(prompt_def)

        # Store original tools list before filtering
        self._all_tools = self.tools.copy()

        # Classify operations
        resources, resource_templates, tools = self.classifier.classify_operations(
            self.operation_map
        )

        logger.info(
            f"Classified endpoints: {len(resources)} resources, "
            f"{len(resource_templates)} templates, {len(tools)} tools"
        )

        # Build resources
        self.resource_handler.build_resources(resources, resource_templates)

        # Handle tool filtering based on dual exposure setting
        if self.auto_convert_resources:
            if self.dual_exposure:
                # Keep all original tools - GET endpoints work as both tools and resources
                self.tools = self._all_tools
                logger.info(
                    f"Dual exposure enabled: {len(resources)} endpoints available as both tools and resources"
                )
            else:
                # Filter out resources from tools (original behavior)
                self.tools = [t for t in self._all_tools if t.name in tools]
                logger.info(f"Filtered tools from {len(self._all_tools)} to {len(self.tools)}")

        # Add resource handlers to MCP server
        self._setup_resource_handlers()

        # Add prompt handlers to MCP server
        self._setup_prompt_handlers()

    def _setup_resource_handlers(self) -> None:
        """Set up resource-related handlers on the MCP server."""
        mcp_server = self.server

        @mcp_server.list_resources()
        async def handle_list_resources() -> list[types.Resource]:
            """List available resources."""
            return self.resource_handler.resources

        @mcp_server.list_resource_templates()
        async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
            """List available resource templates."""
            return self.resource_handler.resource_templates

        @mcp_server.read_resource()
        async def handle_read_resource(uri: str):
            """Read a resource by URI."""
            # Extract HTTP request info from context if available
            http_request_info = None
            try:
                request_context = mcp_server.request_context
                if request_context and hasattr(request_context, "request"):
                    http_request = request_context.request
                    if http_request and hasattr(http_request, "method"):
                        from fastapi_mcp.types import HTTPRequestInfo

                        http_request_info = HTTPRequestInfo(
                            method=http_request.method,
                            path=http_request.url.path,
                            headers=dict(http_request.headers),
                            cookies=http_request.cookies,
                            query_params=dict(http_request.query_params),
                            body=None,
                        )
            except (LookupError, AttributeError) as e:
                logger.debug(f"Could not extract HTTP request info: {e}")

            # Read the resource
            try:
                contents = await self.resource_handler.read_resource(
                    uri=uri,
                    api_executor=self._execute_api_tool,
                    client=self._http_client,
                    http_request_info=http_request_info,
                )

                logger.debug(f"Resource contents type: {type(contents)}, value: {contents}")

                # The MCP server expects an iterable of objects with .content and .mime_type
                # Convert our TextResourceContents to the expected format
                from dataclasses import dataclass

                @dataclass
                class ResourceContent:
                    content: str
                    mime_type: str

                result = []
                for item in contents:
                    if hasattr(item, 'text'):
                        # TextResourceContents has 'text' not 'content'
                        result.append(ResourceContent(
                            content=item.text,
                            mime_type=item.mimeType if hasattr(item, 'mimeType') else "application/json"
                        ))

                return result
            except Exception as e:
                logger.error(f"Error reading resource {uri}: {e}", exc_info=True)
                raise

    def _setup_prompt_handlers(self) -> None:
        """Set up prompt-related handlers on the MCP server."""
        mcp_server = self.server

        @mcp_server.list_prompts()
        async def handle_list_prompts() -> list[types.Prompt]:
            """List available prompts."""
            return self.prompt_handler.prompts

        @mcp_server.get_prompt()
        async def handle_get_prompt(
            name: str, arguments: Optional[Dict[str, str]] = None
        ) -> types.GetPromptResult:
            """Get a prompt with arguments filled."""
            return self.prompt_handler.get_prompt(name, arguments or {})

    def add_prompt(
        self,
        name: str,
        description: str,
        messages: List[Dict[str, str]],
        arguments: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        Add a custom prompt.

        Args:
            name: Prompt name
            description: Prompt description
            messages: List of message templates
            arguments: Optional list of argument definitions
        """
        from .types import PromptMessage, PromptArgument

        prompt_def = PromptDefinition(
            name=name,
            description=description,
            messages=[
                PromptMessage(role=msg["role"], content=msg["content"]) for msg in messages
            ],
            arguments=[
                PromptArgument(
                    name=arg["name"],
                    description=arg.get("description", ""),
                    required=arg.get("required", True),
                    default=arg.get("default"),
                )
                for arg in (arguments or [])
            ],
        )
        self.prompt_handler.add_prompt(prompt_def)