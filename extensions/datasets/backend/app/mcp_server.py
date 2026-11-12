# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Datasets' MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token). A file is not a tool's argument: datasets_upload_target says where
to send it."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "datasets_list",
    "datasets_get",
    "datasets_list_versions",
    "datasets_get_version",
    "datasets_upload_target",
    "datasets_import_huggingface",
    "datasets_rows",
    "datasets_set_mapping",
    "datasets_training",
]
# A route that reads a split from MLflow before it answers takes longer than fastapi-mcp's 10 seconds
TIMEOUT = httpx.Timeout(300.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="datasets",
        description="Thinkube's Datasets: training data for Forge, kept as Parquet in numbered versions",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
