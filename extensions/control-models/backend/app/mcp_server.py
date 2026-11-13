# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Models' MCP server, at /api/v1/mcp: each tool is an API route of TOOLS,
called with the Authorization header of the MCP request, so a tool acts with
the rights of the caller's token (a Thinkube Control API token, or a Keycloak
token)."""

import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.core.config import settings

TOOLS = [
    "models_get_catalog",
    "models_list_mirrors",
    "models_count_active_mirrors",
    "models_get_mirror",
    "models_check_mlflow",
    "models_mirror",
    "models_cancel_mirror",
    "models_reset_mirror",
    "models_delete",
    "models_register_finetuned",
]
# A fine-tune's registration waits for Control's LLM Gateway to read the catalogue again
TIMEOUT = httpx.Timeout(180.0)
PATH = f"{settings.API_V1_STR}/mcp"


def mount(app: FastAPI) -> FastApiMCP:
    server = FastApiMCP(
        app,
        name="control-models",
        description="Thinkube's Models: the model catalogue, the mirrors that copy a catalogue model "
                    "from Hugging Face or Ollama into MLflow, and the publishing of fine-tuned models",
        include_operations=TOOLS,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                      base_url="http://apiserver", timeout=TIMEOUT),
    )
    server.mount_http(mount_path=PATH)
    return server
