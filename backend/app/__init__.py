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

# Import models to ensure they're registered with Base
from app.core.api_tokens import APIToken
from app.models.services import Service, ServiceHealth, ServiceAction, ServiceEndpoint
from app.models.favorites import UserFavorite
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.models.custom_images import CustomImageBuild
from app.models.jupyter_venvs import JupyterVenv
from app.services import health_checker, ServiceDiscovery
from app.services.llm_model_registry import llm_model_registry
from app.services.llm_backend_discovery import llm_backend_discovery
from app.services.llm_ollama_client import ollama_client
from app.services.llm_pod_manager import llm_pod_manager

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

    # Add architectures_built column to jupyter_venvs if it doesn't exist
    try:
        from sqlalchemy import text

        with get_engine().connect() as conn:
            result = conn.execute(
                text(
                    """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='jupyter_venvs' AND column_name='architectures_built'
            """
                )
            )
            if not result.fetchone():
                conn.execute(
                    text(
                        "ALTER TABLE jupyter_venvs ADD COLUMN architectures_built JSON"
                    )
                )
                conn.commit()
                logger.info("Added architectures_built column to jupyter_venvs table")
    except Exception as e:
        logger.warning(f"Could not add architectures_built column: {e}")

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

    # Initialize Jupyter venv templates
    try:
        from app.db.init_venvs import init_venvs

        init_venvs()
    except Exception as e:
        logger.warning(f"Failed to initialize Jupyter venv templates: {e}")

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

    # Reconcile gateway-managed pods before backend discovery starts
    await llm_pod_manager.reconcile()
    logger.info("LLM pod manager reconciled")

    # Start LLM backend discovery first so registry reconciliation has backend data
    llm_discovery_task = asyncio.create_task(llm_backend_discovery.start_polling())
    logger.info("Started LLM backend discovery polling")
    llm_registry_task = asyncio.create_task(llm_model_registry.start_polling())
    logger.info("Started LLM model registry polling")

    yield

    # Shutdown: Clean up resources
    health_checker.stop()
    llm_model_registry.stop()
    llm_backend_discovery.stop()

    health_check_task.cancel()
    discovery_task.cancel()
    llm_registry_task.cancel()
    llm_discovery_task.cancel()
    for task in [health_check_task, discovery_task, llm_registry_task, llm_discovery_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await llm_backend_discovery.close()
    await ollama_client.close()


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
                # === Auth & Tokens ===
                "get_user_info",               # Resource
                "list_tokens",                 # Resource
                "verify_current_token",        # Resource
                "create_token",                # Tool
                "delete_token",                # Tool

                # === Services ===
                "list_services_minimal",       # Resource
                "get_service_details",         # Resource
                "get_service_health_history",  # Resource
                "get_service_dependencies",    # Resource
                "describe_pod",                # Resource
                "get_container_logs",          # Resource
                "toggle_service",              # Tool
                "restart_service",             # Tool
                "trigger_health_check",        # Tool
                "sync_services",               # Tool

                # === Dashboards ===
                "list_dashboards",             # Resource
                "get_dashboard_categories",    # Resource
                "get_dashboard",               # Resource

                # === Templates & Deployments ===
                "list_templates",              # Resource
                "get_template_metadata",       # Resource
                "list_deployments",            # Resource
                "get_deployment_status",       # Resource
                "get_deployment_logs",         # Resource
                "get_deployment_debug_logs",   # Resource
                "download_debug_log",          # Resource
                "deploy_template",             # Tool
                "redeploy_template",           # Tool
                "cancel_deployment",           # Tool

                # === Harbor Images ===
                "list_harbor_images",          # Resource
                "get_harbor_image",            # Resource
                "get_image_statistics",        # Resource
                "list_harbor_jobs",            # Resource
                "get_harbor_job_status",       # Resource
                "list_harbor_projects",        # Resource
                "check_harbor_health",         # Resource
                "register_harbor_image",       # Tool
                "remirror_harbor_image",       # Tool
                "bulk_mirror_images",          # Tool
                "delete_image",                # Tool

                # === Secrets ===
                "list_secrets",                # Resource
                "get_secret",                  # Resource
                "get_secret_apps",             # Resource
                "create_secret",               # Tool
                "update_secret",               # Tool
                "delete_secret",               # Tool

                # === Custom Images ===
                "list_custom_images",          # Resource
                "get_custom_image",            # Resource
                "get_base_registry",           # Resource
                "get_image_dockerfile",        # Resource
                "get_build_logs",              # Resource
                "download_build_log",          # Resource
                "create_custom_image",         # Tool
                "build_custom_image",          # Tool
                "delete_custom_image",         # Tool

                # === Models ===
                "get_model_catalog",           # Resource
                "list_mirror_jobs",            # Resource
                "get_mirror_status",           # Resource
                "check_mlflow_status",         # Resource
                "submit_model_mirror",         # Tool
                "cancel_model_mirror",         # Tool
                "reset_mirror_job",            # Tool
                "delete_model",                # Tool
                "register_finetuned_model",    # Tool

                # === Jupyter Venvs ===
                "list_jupyter_venvs",          # Resource
                "get_jupyter_venv",            # Resource
                "get_venv_templates",          # Resource
                "get_venv_template_details",   # Resource
                "get_venv_build_logs",         # Resource
                "download_venv_build_log",     # Resource
                "create_jupyter_venv",         # Tool
                "build_jupyter_venv",          # Tool
                "delete_jupyter_venv",         # Tool

                # === JupyterHub Config ===
                "get_jupyterhub_config",       # Resource

                # === Optional Components ===
                "list_optional_components",    # Resource
                "get_component_info",          # Resource
                "get_component_status",        # Resource
                "install_optional_component",  # Tool
                "uninstall_optional_component", # Tool

                # === Knative ===
                "list_knative_services",       # Resource
                "get_knative_service",         # Resource

                # === Cluster & GPU ===
                "get_cluster_resources",       # Resource
                "get_gpu_metrics",             # Resource

                # === LLM Gateway ===
                "get_llm_models",              # Resource
                "get_llm_model_status",        # Resource
                "resolve_llm_model",           # Resource
                "get_llm_backends",            # Resource
                "get_llm_gpu_status",          # Resource
                "get_llm_load_options",        # Resource
                "refresh_llm_registry",        # Tool
                "load_llm_model",              # Tool
                "unload_llm_model",            # Tool

                # === Debug ===
                "resolve_hostname",            # Resource
                "test_connectivity",           # Resource
                "get_environment",             # Resource
                "test_ssh",                    # Resource
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
