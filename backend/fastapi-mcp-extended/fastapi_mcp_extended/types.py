"""Type definitions for fastapi-mcp-extended."""

from typing import List, Dict, Optional, Any, Pattern
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
    """Definition of a prompt argument."""

    name: str
    description: str
    required: bool = True
    default: Optional[Any] = None


@dataclass
class PromptMessage:
    """A message template in a prompt."""

    role: Literal["user", "assistant", "system"]
    content: str  # Can include {{variable}} placeholders


@dataclass
class PromptDefinition:
    """Definition of a prompt template."""

    name: str
    description: str
    messages: List[PromptMessage]
    arguments: Optional[List[PromptArgument]] = None