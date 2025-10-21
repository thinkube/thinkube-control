"""Prompt handling for MCP protocol."""

import re
from typing import Dict, List, Any, Optional
import mcp.types as types
from .types import PromptDefinition, PromptArgument, PromptMessage


class PromptHandler:
    """Handles prompt templates and management."""

    def __init__(self):
        """Initialize prompt handler."""
        self.prompts: List[types.Prompt] = []
        self._prompt_definitions: Dict[str, PromptDefinition] = {}

    def add_prompt(self, prompt_def: PromptDefinition) -> None:
        """
        Add a prompt definition.

        Args:
            prompt_def: Prompt definition to add
        """
        # Store the full definition
        self._prompt_definitions[prompt_def.name] = prompt_def

        # Create MCP prompt object
        mcp_prompt = types.Prompt(
            name=prompt_def.name,
            description=prompt_def.description,
            arguments=[
                types.PromptArgument(
                    name=arg.name,
                    description=arg.description,
                    required=arg.required,
                )
                for arg in (prompt_def.arguments or [])
            ],
        )
        self.prompts.append(mcp_prompt)

    def add_default_prompts(self) -> None:
        """Add default prompts for common operations."""
        default_prompts = [
            PromptDefinition(
                name="deploy-application",
                description="Deploy a new application from a template",
                messages=[
                    PromptMessage(
                        role="user",
                        content="Help me deploy a new application called {{app_name}} using the {{template}} template.",
                    ),
                    PromptMessage(
                        role="assistant",
                        content="I'll help you deploy {{app_name}} using the {{template}} template. Let me check available templates and guide you through the configuration.",
                    ),
                ],
                arguments=[
                    PromptArgument(
                        name="app_name",
                        description="Name for the new application",
                        required=True,
                    ),
                    PromptArgument(
                        name="template",
                        description="Template to use (e.g., webapp, vllm, stable-diffusion)",
                        required=False,
                    ),
                ],
            ),
            PromptDefinition(
                name="troubleshoot-service",
                description="Diagnose issues with a service",
                messages=[
                    PromptMessage(
                        role="user",
                        content="Help me troubleshoot the {{service_name}} service.",
                    ),
                    PromptMessage(
                        role="assistant",
                        content="I'll help diagnose issues with {{service_name}}. Let me check its status, recent logs, and dependencies.",
                    ),
                ],
                arguments=[
                    PromptArgument(
                        name="service_name",
                        description="Name of the service to troubleshoot",
                        required=True,
                    ),
                ],
            ),
            PromptDefinition(
                name="system-health",
                description="Check overall system health",
                messages=[
                    PromptMessage(
                        role="user",
                        content="Perform a health check of the Thinkube cluster.",
                    ),
                    PromptMessage(
                        role="assistant",
                        content="I'll perform a comprehensive health check of your Thinkube cluster, including all core services, resource usage, and recent deployments.",
                    ),
                ],
                arguments=[],
            ),
            PromptDefinition(
                name="register-image",
                description="Register a Docker image in Harbor",
                messages=[
                    PromptMessage(
                        role="user",
                        content="Help me register the Docker image {{image_url}} in the Harbor registry.",
                    ),
                    PromptMessage(
                        role="assistant",
                        content="I'll help you register {{image_url}} in Harbor. Let me validate the image and set up mirroring.",
                    ),
                ],
                arguments=[
                    PromptArgument(
                        name="image_url",
                        description="Docker image URL (e.g., docker.io/library/nginx:latest)",
                        required=True,
                    ),
                ],
            ),
        ]

        for prompt_def in default_prompts:
            self.add_prompt(prompt_def)

    def get_prompt(self, name: str, arguments: Dict[str, str]) -> types.GetPromptResult:
        """
        Get a prompt with arguments filled in.

        Args:
            name: Name of the prompt
            arguments: Arguments to fill in the template

        Returns:
            Filled prompt result
        """
        if name not in self._prompt_definitions:
            raise ValueError(f"Prompt '{name}' not found")

        prompt_def = self._prompt_definitions[name]

        # Fill in the template with arguments
        messages = []
        for msg in prompt_def.messages:
            content = msg.content
            # Replace {{variable}} with actual values
            for key, value in arguments.items():
                content = content.replace(f"{{{{{key}}}}}", value)

            messages.append(
                types.PromptMessage(
                    role=types.Role(msg.role),
                    content=types.TextContent(type="text", text=content),
                )
            )

        return types.GetPromptResult(
            description=prompt_def.description,
            messages=messages,
        )