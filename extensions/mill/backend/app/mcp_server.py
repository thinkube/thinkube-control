# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Mill's MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token)."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "mill_list_runs",
    "mill_get_run",
    "mill_get_run_samples",
    "mill_get_run_failures",
    "mill_get_run_log",
    "mill_describe_tasks",
    "mill_start_run",
    "mill_stop_run",
    "mill_run_on_node",
    "mill_list_jobs",
    "mill_get_job",
    "mill_get_job_log",
    "mill_stop_job",
]
# Reading a file's tasks loads the file, which may import heavy libraries
TIMEOUT = httpx.Timeout(120.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="mill",
        description="Thinkube Mill: the runs of data transformation tasks, with their progress, samples, "
                    "failures and logs, starting and stopping a run, and running a task on a chosen node when the "
                    "Scheduler gives it its turn",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
