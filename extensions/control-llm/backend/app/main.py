# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/main.py
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from fastapi.responses import JSONResponse
from app.api import internal
from app.api.router import api_router
# Import models to ensure they're registered with Base
from app.core.api_tokens import APIToken
from app.services.llm_backend_discovery import llm_backend_discovery
from app.services.llm_backend_types import llm_backend_types
from app.services.llm_model_registry import llm_model_registry
from app.services.llm_pod_manager import llm_pod_manager
from app.services.control_services import ControlUnavailableError
from app.services.models_catalog import CatalogUnavailableError


logger = logging.getLogger(__name__)


async def background_work() -> None:
    """Take over the gateway's pods, then follow the backends and the catalogue until the app stops."""
    # The backend types name the namespaces the pod manager reconciles
    await asyncio.to_thread(llm_backend_types.refresh)
    await llm_pod_manager.reconcile()
    # Discovery first, so the registry's reconciliation has the backends
    await asyncio.gather(llm_backend_discovery.start_polling(), llm_model_registry.start_polling())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The database tables are made by the Alembic migrations, which start.sh runs before the app
    task = asyncio.create_task(background_work()) if settings.BACKGROUND_WORK else None
    yield
    if task:
        llm_model_registry.stop()
        llm_backend_discovery.stop()
        task.cancel()
        await llm_backend_discovery.close()

# Initialize FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan
)

# Set up CORS
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include the API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# The routes for the LLM proxy and Control, outside /api (app/api/internal.py)
app.include_router(internal.router)


@app.exception_handler(CatalogUnavailableError)
@app.exception_handler(ControlUnavailableError)
async def another_part_did_not_answer(request, exc: Exception):
    """The extension Models or Control did not answer: the gateway's answer names which, and why."""
    return JSONResponse(status_code=502, content={"detail": str(exc)})


# The MCP server, over the routes above
from app import mcp_server  # noqa: E402

mcp = mcp_server.mount(app)

# Root endpoint
@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "docs_url": f"{settings.API_V1_STR}/docs",
        "redoc_url": f"{settings.API_V1_STR}/redoc",
        "openapi_url": f"{settings.API_V1_STR}/openapi.json",
        "version": "1.0.0",
    }

# Health check endpoint
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "component": "backend"
    }

# Export the app for uvicorn
__all__ = ["app"]
