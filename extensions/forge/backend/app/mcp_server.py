# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Forge's MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token), and passes that token on to Control and Datasets."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "forge_list_models",
    "forge_list_nodes",
    "forge_list_datasets",
    "forge_list_dataset_versions",
    "forge_preview_example",
    "forge_create_run",
    "forge_list_runs",
    "forge_get_run",
    "forge_run_progress",
    "forge_plan_again",
    "forge_queue_run",
    "forge_set_run_priority",
    "forge_set_run_resources",
    "forge_pause_run",
    "forge_resume_run",
    "forge_cancel_run",
    "forge_queue",
    "forge_order_queue",
    "forge_list_slots",
    "forge_create_slot",
    "forge_update_slot",
    "forge_delete_slot",
    "forge_get_priorities",
    "forge_worker_status",
]
# Creating a run calls Control, Datasets and MLflow in turn, longer than fastapi-mcp's 10 seconds
TIMEOUT = httpx.Timeout(300.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="forge",
        description="Thinkube's Forge: fine-tune language models with Meep² on datasets of Datasets",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
