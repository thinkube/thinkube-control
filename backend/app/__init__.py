# app/__init__.py
from contextlib import asynccontextmanager
import asyncio
import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
from fastapi_mcp_extended import ExtendedFastApiMCP
from app.core.config import settings
from app.api.router import api_router
from app.db.session import Base, get_engine, SessionLocal
from app.db.cicd_session import get_cicd_engine

# Import models to ensure they're registered with Base
from app.core.api_tokens import APIToken
from app.models.cicd import Pipeline, PipelineStage, PipelineMetric
from app.models.services import Service, ServiceHealth, ServiceAction, ServiceEndpoint
from app.models.favorites import UserFavorite
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.models.custom_images import CustomImageBuild
from app.services import health_checker, ServiceDiscovery

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    # Startup: Create database tables
    # Create tables in main database (for auth/tokens)
    Base.metadata.create_all(bind=get_engine())

    # Add order_index column if it doesn't exist
    try:
        from sqlalchemy import text

        with get_engine().connect() as conn:
            # Check if order_index column exists
            result = conn.execute(
                text(
                    """
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='user_favorites' AND column_name='order_index'
            """
                )
            )
            if not result.fetchone():
                # Add the column if it doesn't exist
                conn.execute(
                    text(
                        "ALTER TABLE user_favorites ADD COLUMN order_index INTEGER DEFAULT 0"
                    )
                )
                conn.commit()
                logger.info("Added order_index column to user_favorites table")
    except Exception as e:
        logger.warning(f"Could not add order_index column: {e}")

    # Create tables in CI/CD monitoring database
    # Note: The tables already exist from our PostgreSQL setup,
    # but this ensures they're created if missing
    from app.models.cicd import Base as CICDBase

    CICDBase.metadata.create_all(bind=get_cicd_engine())

    # Initialize services in database
    try:
        from app.db.init_services import init_services

        init_services()
    except Exception as e:
        # If it's a duplicate key error, the services are already initialized
        if "duplicate key value violates unique constraint" in str(e):
            logger.info("Services already initialized, skipping")
        else:
            logger.error(f"Failed to initialize services: {e}")

    # Initialize container images in database
    try:
        from app.db.init_images import init_images

        init_images()
    except Exception as e:
        # Log but don't fail startup - images can be synced later via API
        logger.warning(f"Failed to initialize container images: {e}")
        logger.info("Container images can be synced later via /api/v1/harbor/images/sync")


    # Start health check background task
    health_check_task = asyncio.create_task(health_checker.start())
    logger.info("Started health check background task")

    # Start periodic discovery task as backup (every 5 minutes)
    async def periodic_discovery():
        """Run service discovery periodically as a backup"""
        from app.db.session import SessionLocal
        from app.services import ServiceDiscovery

        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                session_factory = SessionLocal()
                db = session_factory()
                try:
                    discovery = ServiceDiscovery(db, settings.DOMAIN_NAME)
                    discovery.discover_all()
                    logger.info("Periodic service discovery completed")
                except Exception as e:
                    logger.error(f"Periodic discovery failed: {e}")
                finally:
                    db.close()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic discovery task: {e}")

    discovery_task = asyncio.create_task(periodic_discovery())
    logger.info("Started periodic discovery task (5 minute interval)")

    yield

    # Shutdown: Clean up resources
    health_checker.stop()
    health_check_task.cancel()
    discovery_task.cancel()
    try:
        await health_check_task
    except asyncio.CancelledError:
        pass
    try:
        await discovery_task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    """Factory function to create FastAPI app with MCP server."""
    # First, set up routes and create MCP before creating the app
    # This is needed to properly combine lifespans

    # Create the FastAPI app
    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        docs_url=f"{settings.API_V1_STR}/docs",
        redoc_url=f"{settings.API_V1_STR}/redoc",
        lifespan=app_lifespan,
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

    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "message": "Welcome to the K8s Dashboard Hub API",
            "docs_url": f"{settings.API_V1_STR}/docs",
            "redoc_url": f"{settings.API_V1_STR}/redoc",
            "openapi_url": f"{settings.API_V1_STR}/openapi.json",
            "version": "1.0.0",
        }

    # Health check endpoint
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Set up MCP using extended fastapi-mcp with resource support
    try:
        # Create Extended MCP server with resource and prompt support
        mcp = ExtendedFastApiMCP(
            app,
            # Enable automatic resource conversion for GET endpoints
            auto_convert_resources=True,
            # Add default prompts for common operations
            add_default_prompts=True,
            # Include all operations (will be automatically classified)
            include_operations=[
                # Services management - will become resources
                "list_services_minimal",  # Resource
                "get_service_details",    # Resource
                "toggle_service",         # Tool
                "restart_service",        # Tool
                # Templates and deployments
                "list_templates",         # Resource
                "get_template_metadata",  # Resource
                "deploy_template",        # Tool
                "list_deployments",       # Resource
                "get_deployment_status",  # Resource
                "get_deployment_logs",    # Resource
                # Dashboards
                "list_dashboards",        # Resource
                # Harbor image registry
                "list_harbor_images",     # Resource
                "get_harbor_image",       # Resource
                "register_harbor_image",  # Tool
                "remirror_harbor_image",  # Tool
                "bulk_mirror_images",     # Tool
                # Optional components
                "list_optional_components",    # Resource
                "get_component_info",          # Resource
                "install_optional_component",  # Tool
                "uninstall_optional_component", # Tool
                "get_component_status",        # Resource
                # Auth
                "get_user_info",          # Resource
            ]
        )

        # Mount the MCP server with HTTP transport
        mcp.mount_http()
        logger.info("Extended MCP server mounted at /mcp with resources, tools, and prompts")
        print("INFO: Extended MCP server mounted at /mcp with resources, tools, and prompts")

    except Exception as e:
        logger.error(f"Failed to set up MCP server: {e}")
        print(f"ERROR: Failed to set up MCP server: {e}")

    return app


# Create the app instance for production use
app = create_app()

# Export the app and factory for uvicorn and tests
__all__ = ["app", "create_app"]
