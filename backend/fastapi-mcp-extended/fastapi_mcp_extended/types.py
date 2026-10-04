# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Type definitions for fastapi-mcp-extended."""

from typing import List, Optional, Pattern
from dataclasses import dataclass
from typing_extensions import Literal


@dataclass
class ResourceMapping:
    """Configuration for mapping endpoints to resources."""

    # Patterns to identify resource endpoints
    resource_patterns: Optional[List[Pattern]] = None

    # Patterns to identify tool endpoints (override resource detection)
    tool_patterns: Optional[List[Pattern]] = None

    # Auto-convert GET endpoints to resources
    auto_convert_gets: bool = True

    # Exclude certain paths from resource conversion
    exclude_paths: Optional[List[str]] = None

    # Cache duration for resources in seconds (0 = no cache)
    cache_duration: int = 60


@dataclass
class PromptArgument:
    """Definition of a prompt argument.

    A required argument has no default: the caller must give it. An optional
    argument has a default, which fills its placeholder when the caller does
    not give it. Any other combination is refused when the prompt is added.
    """

    name: str
    description: str
    required: bool = True
    default: Optional[str] = None


@dataclass
class PromptMessage:
    """A message template in a prompt.

    The MCP protocol allows only the user and assistant roles in a prompt.
    """

    role: Literal["user", "assistant"]
    content: str  # Can include {{variable}} placeholders


@dataclass
class PromptDefinition:
    """Definition of a prompt template."""

    name: str
    description: str
    messages: List[PromptMessage]
    arguments: Optional[List[PromptArgument]] = None
