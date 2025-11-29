"""
Template deployment API endpoints
Handles downloading and executing templates from GitHub
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query, Request
from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, Any, List
from uuid import UUID, uuid4
import tempfile
import shutil
from pathlib import Path
import asyncio
import logging
import os

from sqlalchemy.orm import Session
from sqlalchemy import desc, and_

from app.core.api_tokens import get_current_user_dual_auth
from app.utils.copier_generator import CopierGenerator
from app.db.session import get_db
from app.models.deployments import TemplateDeployment, DeploymentLog
from app.models.deployment_schemas import (
    TemplateDeployAsyncRequest,
    DeploymentResponse,
    DeploymentStatus,
    DeploymentLogsResponse,
    DeploymentLogEntry,
    DeploymentListResponse,
)
from pathlib import Path
from app.services.background_executor import background_executor
from app.services.dependency_manager import DependencyManager
from app.services.model_downloader import ModelDownloaderService
import yaml
import aiohttp

logger = logging.getLogger(__name__)
router = APIRouter(tags=["templates"])


def _extract_domain_from_url():
    """Extract domain from FRONTEND_URL or KEYCLOAK_URL"""
    frontend_url = os.environ.get("FRONTEND_URL", "")
    if frontend_url:
        # Extract domain from https://control.example.com -> example.com
        from urllib.parse import urlparse

        parsed = urlparse(frontend_url)
        if parsed.hostname:
            # Remove subdomain (control.) to get base domain
            parts = parsed.hostname.split(".")
            if len(parts) > 2:
                return ".".join(parts[-2:])
            return parsed.hostname

    # Fallback to KEYCLOAK_URL
    keycloak_url = os.environ.get("KEYCLOAK_URL", "")
    if keycloak_url:
        from urllib.parse import urlparse

        parsed = urlparse(keycloak_url)
        if parsed.hostname:
            # Remove subdomain (auth.) to get base domain
            parts = parsed.hostname.split(".")
            if len(parts) > 2:
                return ".".join(parts[-2:])
            return parsed.hostname

    # No domain found - this is a critical error
    raise RuntimeError(
        "Cannot determine domain_name from FRONTEND_URL or KEYCLOAK_URL environment variables"
    )


class TemplateParameter(BaseModel):
    """Template parameter definition"""

    name: str
    type: str  # str, bool, int, choice
    description: str
    default: Optional[Any] = None
    required: Optional[bool] = True
    # Type-specific fields
    choices: Optional[list[str]] = None  # For choice type
    pattern: Optional[str] = None  # For str type
    min: Optional[int] = None  # For int type
    max: Optional[int] = None  # For int type
    minLength: Optional[int] = None  # For str type
    maxLength: Optional[int] = None  # For str type
    placeholder: Optional[str] = None
    group: Optional[str] = None
    order: Optional[int] = None
    # Dynamic choice fields
    dynamic_source: Optional[str] = None  # e.g., "model_catalog"
    filter: Optional[Dict[str, Any]] = None  # Filter criteria for dynamic choices


class TemplateMetadata(BaseModel):
    """Template metadata from template.yaml"""

    apiVersion: str
    kind: str
    metadata: Dict[str, Any]
    parameters: list[TemplateParameter]


@router.get("/list", operation_id="list_templates")
async def list_available_templates(
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    List available templates from repositories.json metadata

    Dynamically discovers application templates from the thinkube-metadata repository.
    """
    try:
        # Fetch repositories.json from GitHub (raw.githubusercontent.com)
        metadata_url = "https://raw.githubusercontent.com/thinkube/thinkube-metadata/main/repositories.json"

        async with aiohttp.ClientSession() as session:
            async with session.get(metadata_url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch repositories.json: {response.status}")
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to fetch template metadata from thinkube-metadata repository"
                    )

                # GitHub raw files return text/plain, so we need to explicitly allow JSON parsing
                metadata = await response.json(content_type=None)

        # Filter for application_template type
        templates = []
        for repo in metadata.get("repositories", []):
            if repo.get("type") == "application_template":
                templates.append({
                    "name": repo["name"],
                    "description": repo.get("description", ""),
                    "url": repo.get("github_url", ""),
                    "org": repo.get("org", "thinkube"),
                })

        logger.info(f"Discovered {len(templates)} application templates")
        return {"templates": templates}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing templates: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list templates: {str(e)}"
        )


@router.get(
    "/metadata", response_model=TemplateMetadata, operation_id="get_template_metadata"
)
async def get_template_metadata(
    template_url: str, current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    Fetch template metadata from template.yaml

    Downloads template.yaml from the GitHub repository and parses it
    to extract parameter definitions for dynamic form generation.
    """
    try:
        # Extract org and repo from URL
        url_parts = template_url.rstrip("/").split("/")
        if len(url_parts) < 2:
            raise HTTPException(status_code=400, detail="Invalid GitHub URL format")

        org = url_parts[-2]
        repo = url_parts[-1]

        # Try to fetch manifest.yaml first, then template.yaml for backward compatibility
        manifest_urls = [
            f"https://raw.githubusercontent.com/{org}/{repo}/main/manifest.yaml",
            f"https://raw.githubusercontent.com/{org}/{repo}/master/manifest.yaml",
            f"https://raw.githubusercontent.com/{org}/{repo}/main/template.yaml",  # backward compat
            f"https://raw.githubusercontent.com/{org}/{repo}/master/template.yaml",  # backward compat
        ]

        content = None
        async with aiohttp.ClientSession() as session:
            for url in manifest_urls:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.text()
                        logger.info(f"Found template manifest at: {url}")
                        break

            if content is None:
                raise HTTPException(
                    status_code=404,
                    detail="Template does not have a manifest.yaml or template.yaml file. All templates must include this manifest.",
                )

        # Parse template.yaml
        try:
            template_data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid YAML in template.yaml: {str(e)}"
            )

        # Validate and extract metadata
        if not template_data or template_data.get("apiVersion") != "thinkube.io/v1":
            # Not a valid Thinkube template
            raise HTTPException(
                status_code=400,
                detail="Invalid template.yaml: must have apiVersion: thinkube.io/v1",
            )

        # Convert parameters to Pydantic models
        parameters = []
        for param_data in template_data.get("parameters", []):
            # Handle dynamic choices from model catalog
            dynamic_source = param_data.get("dynamic_source")
            filter_criteria = param_data.get("filter", {})
            choices = param_data.get("choices")

            if dynamic_source == "model_catalog":
                # Fetch models from catalog and apply filters
                try:
                    model_service = ModelDownloaderService()
                    available_models = model_service.get_available_models()
                    downloaded_models = model_service.check_all_models_exist()

                    # Apply filters
                    filtered_models = []
                    for model in available_models:
                        # Check server_type filter
                        if "server_type" in filter_criteria:
                            required_type = filter_criteria["server_type"]
                            if required_type not in model.get("server_type", []):
                                continue

                        # Check is_downloaded filter
                        if filter_criteria.get("is_downloaded", False):
                            if not downloaded_models.get(model["id"], False):
                                continue

                        filtered_models.append(model["id"])

                    # Use filtered models as choices
                    choices = filtered_models
                    logger.info(f"Dynamic choices for {param_data['name']}: {len(choices)} models")
                except Exception as e:
                    logger.error(f"Failed to fetch dynamic choices from model catalog: {e}")
                    # Keep static choices or empty list as fallback
                    pass

            param = TemplateParameter(
                name=param_data["name"],
                type=param_data["type"],
                description=param_data.get("description", ""),
                default=param_data.get("default"),
                required=param_data.get("required", True),
                choices=choices,
                pattern=param_data.get("pattern"),
                min=param_data.get("min"),
                max=param_data.get("max"),
                minLength=param_data.get("minLength"),
                maxLength=param_data.get("maxLength"),
                placeholder=param_data.get("placeholder"),
                group=param_data.get("group"),
                order=param_data.get("order"),
                dynamic_source=dynamic_source,
                filter=filter_criteria,
            )
            parameters.append(param)

        return TemplateMetadata(
            apiVersion=template_data["apiVersion"],
            kind=template_data["kind"],
            metadata=template_data.get("metadata", {}),
            parameters=parameters,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch template metadata: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch template metadata: {str(e)}"
        )


@router.post(
    "/deploy-async", response_model=DeploymentResponse, operation_id="deploy_template"
)
async def deploy_template_async(
    request: TemplateDeployAsyncRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    Deploy a template asynchronously

    This endpoint queues a deployment and returns immediately with a deployment ID.
    Use the deployment ID to track progress via the status and logs endpoints.
    """
    try:
        # Extract org and repo from URL
        url_parts = str(request.template_url).rstrip("/").split("/")
        if len(url_parts) < 2:
            raise ValueError("Invalid GitHub URL format")

        # Extract overwrite flag from variables
        overwrite_confirmed = request.variables.pop("_overwrite_confirmed", False)

        # Check for service name conflicts
        dep_manager = DependencyManager(db)
        is_valid, conflict_message = dep_manager.validate_service_name(
            request.template_name, "user_app"
        )

        # If there's a conflict and it's not a user app, reject immediately
        if not is_valid and "user application" not in (conflict_message or ""):
            raise HTTPException(
                status_code=400,
                detail=conflict_message
                or f"Service name '{request.template_name}' is not available",
            )

        # If it's a user app conflict and overwrite not confirmed, return with warning
        if (
            conflict_message
            and "will be overwritten" in conflict_message
            and not overwrite_confirmed
        ):
            return DeploymentResponse(
                deployment_id="",  # No deployment created yet
                status="conflict",
                message=conflict_message,
                requires_confirmation=True,
                websocket_url="",
            )

        # Extract domain for use in defaults
        domain_name = _extract_domain_from_url()

        # Prepare variables with smart defaults
        deployment_vars = {
            "template_url": str(request.template_url),
            "app_name": request.template_name,
            "deployment_namespace": request.template_name,  # Same as app_name - no prefixes
            **request.variables,
            # Add system variables
            "domain_name": domain_name,
            "admin_username": "tkadmin",  # Default admin username
            "github_token": os.environ.get("GITHUB_TOKEN", ""),
            "overwrite_existing": overwrite_confirmed,  # Pass to deployment
        }

        # Provide sensible defaults for standard parameters from CopierGenerator
        # These are the same standard parameters that CopierGenerator always includes
        standard_defaults = {
            "project_name": request.template_name,
            "project_description": deployment_vars.get(
                "app_description", f"A Thinkube application: {request.template_name}"
            ),
            "author_name": current_user.get("preferred_username", "thinkube-user"),
            "author_email": current_user.get("email")
            or f"{current_user.get('preferred_username', 'thinkube-user')}@{domain_name}",
        }

        # Apply standard defaults only if not already provided
        for key, default_value in standard_defaults.items():
            if key not in deployment_vars:
                deployment_vars[key] = default_value

        # Note: Template-specific parameters should have defaults in manifest.yaml
        # If they don't have defaults and aren't provided, the template author
        # intended them to be required, so we let copier handle the validation

        # Create deployment record
        deployment = TemplateDeployment(
            id=uuid4(),
            name=request.template_name,
            template_url=str(request.template_url),
            status="pending",
            variables=deployment_vars,
            created_by=current_user.get("preferred_username") or "unknown",
        )
        db.add(deployment)
        db.commit()

        # Check execution mode
        if request.execution_mode == "background":
            # Start deployment in background for API/MCP usage
            background_tasks.add_task(
                background_executor.start_deployment, str(deployment.id)
            )
            status = "running"
            message = (
                "Deployment started in background. Check status endpoint for progress."
            )
        else:
            # Default WebSocket mode - deployment will start when client connects
            status = "pending"
            message = "Deployment prepared. Connect to WebSocket to start execution."

        return DeploymentResponse(
            deployment_id=str(deployment.id),
            status=status,
            message=message,
            websocket_url=f"/ws/template/deploy/{deployment.id}",
            conflict_warning=(
                conflict_message if conflict_message and overwrite_confirmed else None
            ),
        )

    except Exception as e:
        logger.error(f"Failed to create deployment: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to create deployment: {str(e)}"
        )


@router.get(
    "/deployments",
    response_model=DeploymentListResponse,
    operation_id="list_deployments",
)
async def list_deployments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    List template deployments with pagination

    Optionally filter by status: pending, running, success, failed, cancelled
    """
    query = db.query(TemplateDeployment)

    # Filter by status if provided
    if status:
        query = query.filter(TemplateDeployment.status == status)

    # Filter by user if not admin
    # Check if user has admin role
    is_admin = "admin" in current_user.get("realm_access", {}).get("roles", [])
    if not is_admin:
        query = query.filter(
            TemplateDeployment.created_by == current_user.get("preferred_username")
        )

    # Get total count
    total_count = query.count()

    # Apply pagination
    offset = (page - 1) * page_size
    deployments = (
        query.order_by(desc(TemplateDeployment.created_at))
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return DeploymentListResponse(
        deployments=[DeploymentStatus.model_validate(d.to_dict()) for d in deployments],
        total_count=total_count,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/deployments/{deployment_id}",
    response_model=DeploymentStatus,
    operation_id="get_deployment_status",
)
async def get_deployment_status(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get deployment status by ID"""
    deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()

    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Check permissions
    is_admin = "admin" in current_user.get("realm_access", {}).get("roles", [])
    if not is_admin and deployment.created_by != current_user.get("preferred_username"):
        raise HTTPException(status_code=403, detail="Access denied")

    return DeploymentStatus.model_validate(deployment.to_dict())


@router.get(
    "/deployments/{deployment_id}/logs",
    response_model=DeploymentLogsResponse,
    operation_id="get_deployment_logs",
)
async def get_deployment_logs(
    deployment_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    Get deployment logs with pagination

    Returns logs in chronological order with offset/limit pagination.
    """
    # Check deployment exists and permissions
    deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Check permissions - admin or creator can view logs
    is_admin = "admin" in current_user.get("realm_access", {}).get("roles", [])
    if not is_admin and deployment.created_by != current_user.get("preferred_username"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Get logs
    query = db.query(DeploymentLog).filter_by(deployment_id=deployment_id)
    total_count = query.count()

    logs = query.order_by(DeploymentLog.timestamp).offset(offset).limit(limit).all()

    return DeploymentLogsResponse(
        deployment_id=str(deployment_id),
        logs=[DeploymentLogEntry.model_validate(log.to_dict()) for log in logs],
        total_count=total_count,
        has_more=(offset + limit) < total_count,
    )


@router.delete("/deployments/{deployment_id}")
async def cancel_deployment(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    Cancel a pending or running deployment

    Only deployments in 'pending' or 'running' status can be cancelled.
    """
    deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()

    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Check permissions
    is_admin = "admin" in current_user.get("realm_access", {}).get("roles", [])
    if not is_admin and deployment.created_by != current_user.get("preferred_username"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if deployment can be cancelled
    if deployment.status not in ["pending", "running"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel deployment in '{deployment.status}' status",
        )

    # Cancel the deployment
    cancelled = await background_executor.cancel_deployment(str(deployment_id))

    if not cancelled:
        # Deployment might have just completed
        db.refresh(deployment)
        if deployment.status in ["success", "failed"]:
            raise HTTPException(
                status_code=400, detail=f"Deployment already {deployment.status}"
            )

    return {"message": "Deployment cancellation requested"}


@router.get("/deployments/{deployment_id}/debug-logs")
async def get_deployment_debug_logs(
    deployment_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    🤖 Get debug log files for a deployment

    Returns paths to debug log files stored in /home/shared-logs/deployments/{app_name}/
    """
    # Check deployment exists and permissions
    deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Check permissions
    is_admin = current_user.get("is_admin", False)
    if not is_admin and deployment.created_by != current_user.get("preferred_username"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Find log files - check both possible locations
    app_name = deployment.variables.get("app_name", "unknown")
    shared_log_dir = Path("/home/thinkube/shared-logs/deployments") / app_name
    tmp_log_dir = Path("/tmp/thinkube-deployments") / app_name

    # Check which directory exists
    log_dir = None
    if shared_log_dir.exists():
        log_dir = shared_log_dir
    elif tmp_log_dir.exists():
        log_dir = tmp_log_dir

    debug_logs = []
    if log_dir:
        # Find all log files for this deployment
        for log_file in sorted(log_dir.glob("deployment-*.log"), reverse=True):
            # Get file stats
            stats = log_file.stat()
            debug_logs.append(
                {
                    "filename": log_file.name,
                    "path": str(log_file),
                    "size": stats.st_size,
                    "created": stats.st_ctime,
                    "modified": stats.st_mtime,
                }
            )

        # Also look for variable dumps
        for var_file in sorted(log_dir.glob("deployment-*-vars.yaml"), reverse=True):
            stats = var_file.stat()
            debug_logs.append(
                {
                    "filename": var_file.name,
                    "path": str(var_file),
                    "size": stats.st_size,
                    "created": stats.st_ctime,
                    "modified": stats.st_mtime,
                    "type": "variables",
                }
            )

    return {
        "deployment_id": str(deployment_id),
        "app_name": app_name,
        "log_directory": str(log_dir),
        "debug_logs": debug_logs,
        "message": f"🤖 Found {len(debug_logs)} debug log files",
    }


@router.get("/deployments/{deployment_id}/debug-logs/{filename}")
async def download_debug_log(
    deployment_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    🤖 Download a specific debug log file

    Returns the content of the debug log file
    """
    from fastapi.responses import FileResponse

    # Check deployment exists and permissions
    deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Check permissions
    is_admin = current_user.get("is_admin", False)
    if not is_admin and deployment.created_by != current_user.get("preferred_username"):
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Find the log file - check both possible locations
    app_name = deployment.variables.get("app_name", "unknown")
    shared_log_file = Path("/home/thinkube/shared-logs/deployments") / app_name / filename
    tmp_log_file = Path("/tmp/thinkube-deployments") / app_name / filename

    log_file = None
    if shared_log_file.exists():
        log_file = shared_log_file
    elif tmp_log_file.exists():
        log_file = tmp_log_file
    else:
        raise HTTPException(status_code=404, detail="Log file not found")

    # Return the file
    return FileResponse(path=str(log_file), filename=filename, media_type="text/plain")
