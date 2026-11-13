# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/main.py
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.router import api_router
# Import models to ensure they're registered with Base
from app.core.api_tokens import APIToken
from app.db.session import get_session_local
from app.services import follow
from app.services.image_discovery import ImageDiscovery


logger = logging.getLogger(__name__)


def read_platform_images() -> None:
    """Read the platform's image manifests into the list of images."""
    db = get_session_local()()
    try:
        ImageDiscovery(db).sync_with_configmaps()
    finally:
        db.close()


async def background_work() -> None:
    """At start, read the platform's images; then follow the mirror jobs and builds until the app stops."""
    try:
        await asyncio.to_thread(read_platform_images)
    except Exception:
        logger.exception("the platform's image manifests were not read at start; images_sync reads them again")
    await follow.run()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The database tables are made by the Alembic migrations, which start.sh runs before the app
    task = asyncio.create_task(background_work()) if settings.BACKGROUND_WORK else None
    yield
    if task:
        task.cancel()

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
