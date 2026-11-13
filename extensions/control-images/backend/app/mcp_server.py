# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Images' MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token)."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "images_list",
    "images_get",
    "images_stats",
    "images_list_jobs",
    "images_get_job",
    "images_registry_health",
    "images_mirror",
    "images_remirror",
    "images_mirror_again",
    "images_sync",
    "images_delete",
    "images_list_custom",
    "images_get_custom",
    "images_get_base_registry",
    "images_get_dockerfile",
    "images_get_build_log",
    "images_create_custom",
    "images_build_custom",
    "images_delete_custom",
]
# Listing the pods that use an image, before a delete, takes longer than fastapi-mcp's 10 seconds on a large cluster
TIMEOUT = httpx.Timeout(120.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="control-images",
        description="Thinkube's Images: the container images mirrored into Harbor, the jobs that mirror "
                    "them, and the custom images built from a Dockerfile",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
