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
from app.services.metadata import CatalogUnavailableError
# Import models to ensure they're registered with Base
from app.core.api_tokens import APIToken
from app.services import follow


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The database tables are made by the Alembic migrations, which start.sh runs before the app
    task = asyncio.create_task(follow.run()) if settings.BACKGROUND_WORK else None
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

# Control's own reads, outside /api (app/api/internal.py)
app.include_router(internal.router)


@app.exception_handler(CatalogUnavailableError)
async def catalog_unavailable(request, exc: CatalogUnavailableError):
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
