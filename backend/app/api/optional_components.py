# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
API endpoints for managing optional Thinkube components
"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from typing import List, Dict, Any, Optional
from uuid import uuid4
import logging
import os

from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.api_tokens import get_current_user_dual_auth
from app.services.optional_components import OptionalComponentService
from app.services.background_executor import background_executor
from app.models.deployments import TemplateDeployment
from pydantic import BaseModel
from app.services.metadata_fetcher import CatalogUnavailableError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optional-components"])


class ComponentInstallRequest(BaseModel):
    """Request model for component installation"""
    parameters: Optional[Dict[str, Any]] = {}
    force: Optional[bool] = False


class ComponentResponse(BaseModel):
    """Response model for component information"""
    name: str
    display_name: str
    description: str
    category: str
    icon: str
    installed: bool
    # ``installing`` or ``uninstalling`` while a run for the component is in flight.
    activity: Optional[str] = None
    component_version: Optional[str] = None
    requirements_met: bool
    missing_requirements: List[str]
    estimated_time: int


class ComponentListResponse(BaseModel):
    """Response model for component list"""
    components: List[ComponentResponse]
    

class InstallResponse(BaseModel):
    """Response model for installation request"""
    deployment_id: str
    component: str
    status: str
    message: str
    websocket_url: Optional[str] = None
    # Place in the run queue, 1 being the next to start.
    queue_position: Optional[int] = None


def _enqueue_or_refuse(db: Session, deployment: TemplateDeployment) -> int:
    """Queue the run; the same run already queued or in flight is refused with 409."""
    from app.services.run_queue import DuplicateRun, enqueue

    try:
        return enqueue(db, deployment)
    except DuplicateRun as duplicate:
        raise HTTPException(status_code=409, detail=str(duplicate))


@router.get("/list", response_model=ComponentListResponse, operation_id="list_optional_components")
async def list_optional_components(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    List all available optional components with their installation status
    """
    try:
        service = OptionalComponentService(db)
        components = service.list_components()
        
        # Convert to response model
        component_responses = []
        for comp in components:
            component_responses.append(ComponentResponse(
                name=comp["name"],
                display_name=comp["display_name"],
                description=comp["description"],
                category=comp["category"],
                icon=comp["icon"],
                installed=comp["installed"],
                component_version=comp.get("component_version"),
                requirements_met=comp["requirements_met"],
                missing_requirements=comp["missing_requirements"],
                estimated_time=comp.get("estimated_time", 10)
            ))
        
        return ComponentListResponse(components=component_responses)
        
    except CatalogUnavailableError:
        raise
    except Exception as e:
        logger.error(f"Failed to list optional components: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list optional components: {str(e)}"
        )


@router.get("/{component}/info", operation_id="get_component_info")
async def get_component_info(
    component: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a specific optional component
    """
    try:
        service = OptionalComponentService(db)
        component_info = service.get_component(component)
        
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        return component_info
        
    except HTTPException:
        raise
    except CatalogUnavailableError:
        raise
    except Exception as e:
        logger.error(f"Failed to get component info: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get component info: {str(e)}"
        )


async def _install_from_template(
    component: str,
    component_info: dict,
    template: dict,
    db: Session,
    background_tasks: BackgroundTasks,
    current_user: dict,
) -> InstallResponse:
    """Queue a template-backed component through the template deployment path."""
    from app.core.config import settings

    app_name = template.get("fixed_name") or component
    domain_name = settings.DOMAIN_NAME
    username = current_user.get("preferred_username", "thinkube-user")
    # The same variables deploy_template records: the deploy script reads the
    # template address, the namespace and the domain from them.
    deployment = TemplateDeployment(
        id=uuid4(),
        name=app_name,
        template_url=template["url"],
        status="pending",
        variables={
            "template_url": template["url"],
            "app_name": app_name,
            "deployment_namespace": app_name,
            "domain_name": domain_name,
            "overwrite_existing": True,
            "deployment_type": "component",
            "replace_developer_commits": False,
            "project_name": app_name,
            "project_description": component_info.get("description", ""),
            "author_name": username,
            "author_email": current_user.get("email") or f"{username}@{domain_name}",
        },
        created_by=current_user.get("preferred_username", "unknown"),
    )
    queue_position = _enqueue_or_refuse(db, deployment)

    return InstallResponse(
        deployment_id=str(deployment.id),
        component=component,
        status="queued",
        message=f"Installation of {component_info['display_name']} queued at position {queue_position}; poll get_deployment_status with the deployment id",
        websocket_url=None,
        queue_position=queue_position,
    )


@router.post("/{component}/install", response_model=InstallResponse, operation_id="install_optional_component")
async def install_optional_component(
    component: str,
    request: Optional[ComponentInstallRequest] = None,
    background_tasks: BackgroundTasks = None,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """Install an optional component; answers at once with a deployment id.

    The install runs on the server, detached from this call. Poll
    get_deployment_status with the id: it reports the step in progress, then
    success or failed with the reason; get_deployment_logs has the full log.
    A component that needs something not installed is refused unless force
    is set. The body is optional.
    """
    request = request or ComponentInstallRequest()
    try:
        service = OptionalComponentService(db)
        
        # Validate component exists
        component_info = service.get_component(component)
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        # Validate installation
        validation = service.validate_installation(component)
        if not validation["valid"]:
            if request.force:
                logger.warning(f"Force installing component {component}: {validation.get('error')}")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=validation.get("error", "Component cannot be installed")
                )
        
        # Inference backends install through the template deployment process
        # rather than a playbook; the menu entry is the same either way.
        template = service.template_descriptor(component)
        if template:
            return await _install_from_template(
                component, component_info, template, db, background_tasks, current_user
            )

        # Get playbook path
        playbook_path = service.get_playbook_path(component, "install")
        if not playbook_path:
            raise HTTPException(
                status_code=500,
                detail=f"Installation playbook not found for component '{component}'"
            )
        
        # Create deployment record (reusing TemplateDeployment model)
        deployment = TemplateDeployment(
            id=uuid4(),
            name=f"optional-{component}",
            template_url=f"optional://{component}",  # Special URL format for optional components
            status="pending",
            variables={
                "component": component,
                "parameters": request.parameters,
                "playbook": playbook_path
            },
            created_by=current_user.get("preferred_username", "unknown")
        )
        queue_position = _enqueue_or_refuse(db, deployment)

        return InstallResponse(
            deployment_id=str(deployment.id),
            component=component,
            status="queued",
            message=f"Installation of {component_info['display_name']} queued at position {queue_position}; poll get_deployment_status with the deployment id",
            websocket_url=None,
            queue_position=queue_position,
        )
        
    except HTTPException:
        raise
    except CatalogUnavailableError:
        raise
    except Exception as e:
        logger.error(f"Failed to install component {component}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to install component: {str(e)}"
        )


@router.delete("/{component}", operation_id="uninstall_optional_component")
async def uninstall_optional_component(
    component: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Uninstall an optional component
    """
    try:
        service = OptionalComponentService(db)
        
        # Validate component exists
        component_info = service.get_component(component)
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        # Check if installed
        if not component_info["installed"]:
            raise HTTPException(
                status_code=400,
                detail=f"Component '{component}' is not installed"
            )
        
        # Get uninstall playbook path
        # A template deployment leaves an ArgoCD application, a namespace, a
        # Gitea repository, a checkout and a services row rather than anything
        # a rollback playbook could undo.
        if service.template_descriptor(component):
            from app.core.config import settings
            from app.services.component_teardown import ComponentTeardown

            result = ComponentTeardown(db, settings.DOMAIN_NAME).remove(
                component, component_info["namespace"]
            )
            if result["failed"]:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Removed {', '.join(result['removed']) or 'nothing'}; "
                        f"failed on {'; '.join(result['failed'])}"
                    ),
                )
            return {
                "deployment_id": None,
                "component": component,
                "status": "completed",
                "message": (
                    f"{component_info['display_name']} removed: "
                    f"{', '.join(result['removed']) or 'nothing to remove'}"
                ),
                "websocket_url": None,
            }

        playbook_path = service.get_playbook_path(component, "uninstall")
        if not playbook_path:
            raise HTTPException(
                status_code=500,
                detail=f"Uninstall playbook not found for component '{component}'"
            )
        
        # Create deployment record for uninstallation
        deployment = TemplateDeployment(
            id=uuid4(),
            name=f"uninstall-{component}",
            template_url=f"optional://{component}/uninstall",
            status="pending",
            variables={
                "component": component,
                "action": "uninstall",
                "playbook": playbook_path
            },
            created_by=current_user.get("preferred_username", "unknown")
        )
        queue_position = _enqueue_or_refuse(db, deployment)

        return {
            "deployment_id": str(deployment.id),
            "component": component,
            "status": "queued",
            "message": f"Uninstallation of {component_info['display_name']} queued at position {queue_position}; poll get_deployment_status with the deployment id",
            "websocket_url": None,
            "queue_position": queue_position,
        }
        
    except HTTPException:
        raise
    except CatalogUnavailableError:
        raise
    except Exception as e:
        logger.error(f"Failed to uninstall component {component}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to uninstall component: {str(e)}"
        )


@router.get("/{component}/status", operation_id="get_component_status")
async def get_component_status(
    component: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Get the current status of an optional component
    """
    try:
        service = OptionalComponentService(db)
        
        # Validate component exists
        component_info = service.get_component(component)
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        return component_info["status"]
        
    except HTTPException:
        raise
    except CatalogUnavailableError:
        raise
    except Exception as e:
        logger.error(f"Failed to get component status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get component status: {str(e)}"
        )
