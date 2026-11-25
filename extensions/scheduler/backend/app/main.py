# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.router import api_router
# Import models to ensure they're registered with Base
from app.core.api_tokens import APIToken
from app.models.jobs import Job  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The database tables are managed by Alembic migrations, which start.sh applies before the app starts
    import asyncio

    from app.db.session import get_session_local
    from app.services import nodes, pods, worker

    # The worker follows the jobs and starts the ones that can run
    loop = None
    if settings.SCHEDULER_WORKER:
        loop = asyncio.create_task(worker.run_forever(get_session_local(), worker.Worker(nodes.nodes, pods.Pods())))
    yield
    if loop is not None:
        loop.cancel()

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
