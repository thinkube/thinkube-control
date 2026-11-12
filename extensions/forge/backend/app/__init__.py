# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/__init__.py
import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.router import api_router
# Import models to ensure they're registered with Base
from app.core.api_tokens import APIToken
from app.models.forge import Run
from app.db.session import get_session_local
from app.services import worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Database tables are now managed by Alembic migrations
    # which run in start.sh before the app starts.
    # The training queue's worker runs beside the API, and stops with it.
    task = asyncio.create_task(worker.run_forever(get_session_local())) if settings.QUEUE_WORKER else None
    yield
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

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
