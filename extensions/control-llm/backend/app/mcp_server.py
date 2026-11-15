# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The LLM Gateway's MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token)."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "llm_list_models",
    "llm_get_model_status",
    "llm_resolve_model",
    "llm_list_backends",
    "llm_list_backend_types",
    "llm_get_gpu_status",
    "llm_get_load_options",
    "llm_refresh",
    "llm_load_model",
    "llm_unload_model",
]
# A refresh reads the catalogue from the extension Models and probes every backend
TIMEOUT = httpx.Timeout(120.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="control-llm",
        description="Thinkube's LLM Gateway: the mirrored models with their state, the GPU nodes and "
                    "their memory, the inference backends, and loading and unloading a model on a node",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
