# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Storage's MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token)."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "storage_get",
    "storage_list_folder",
    "storage_set_quota",
    "storage_delete_path",
    "storage_get_nodes",
    "storage_get_claims",
]
# Reading the sizes of the shared folders, or a node's training folder, takes longer than fastapi-mcp's 10 seconds
TIMEOUT = httpx.Timeout(300.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="control-storage",
        description="Thinkube's Storage: the shared folders and their quotas, the disk under them, "
                    "node disks and volume claims",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
