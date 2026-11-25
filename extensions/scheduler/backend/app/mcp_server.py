# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The scheduler's MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token)."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "scheduler_list_nodes",
    "scheduler_create_job",
    "scheduler_list_jobs",
    "scheduler_get_job",
    "scheduler_stop_job",
    "scheduler_get_job_log",
]
TIMEOUT = httpx.Timeout(60.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="scheduler",
        description="Thinkube Scheduler: the nodes, their GPUs and what each one holds, and the jobs it runs on "
                    "them: giving it a job, following it, reading its log and stopping it",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
