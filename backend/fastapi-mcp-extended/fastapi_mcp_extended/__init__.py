"""
FastAPI-MCP-Extended: Extended FastAPI-MCP server with resource and prompt support.

This package extends fastapi-mcp to provide full MCP protocol support including
resources and prompts in addition to tools.
"""

__version__ = "0.1.0"

from .server import ExtendedFastApiMCP
from .types import ResourceMapping, PromptDefinition

__all__ = [
    "ExtendedFastApiMCP",
    "ResourceMapping",
    "PromptDefinition",
]