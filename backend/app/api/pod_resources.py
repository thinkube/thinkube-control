"""Pod resource management API endpoints for in-place resize (K8s 1.35 GA)"""

import logging
from typing import Optional, Dict
from uuid import UUID
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.services import Service as ServiceModel, ServiceAction
from app.services import K8sServiceManager
from app.core.api_tokens import get_current_user_dual_auth


logger = logging.getLogger(__name__)

router = APIRouter()


class PodResourceResizeRequest(BaseModel):
    """Request to resize a pod's container resources in-place."""
    cpu_request: Optional[str] = Field(None, description="CPU request (e.g., '500m', '1')")
    cpu_limit: Optional[str] = Field(None, description="CPU limit (e.g., '1000m', '2')")
    memory_request: Optional[str] = Field(None, description="Memory request (e.g., '256Mi', '1Gi')")
    memory_limit: Optional[str] = Field(None, description="Memory limit (e.g., '512Mi', '2Gi')")


class PodResourceResizeResponse(BaseModel):
    """Response after a pod resource resize operation."""
    success: bool
    message: str
    previous_resources: Optional[Dict] = None
    new_resources: Optional[Dict] = None
    resize_status: Optional[str] = None


@router.get(
    "/{service_id}/pods/{pod_name}/resources",
    operation_id="get_pod_resources",
)
async def get_pod_resources(
    service_id: UUID,
    pod_name: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get detailed resource information for a pod including resize status.

    Returns current spec resources, allocated resources, resize policy,
    and any pending resize conditions (K8s 1.35 GA).
    """
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    k8s_manager = K8sServiceManager()
    result = k8s_manager.get_pod_resource_details(service.namespace, pod_name)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pod '{pod_name}' not found in namespace '{service.namespace}'",
        )

    return result


@router.patch(
    "/{service_id}/pods/{pod_name}/containers/{container_name}/resources",
    response_model=PodResourceResizeResponse,
    operation_id="resize_pod_resources",
)
async def resize_pod_resources(
    service_id: UUID,
    pod_name: str,
    container_name: str,
    resize_request: PodResourceResizeRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Resize a running pod's container resources in-place (K8s 1.35 GA).

    Uses InPlacePodVerticalScaling to modify CPU and memory without pod
    recreation. CPU changes take effect immediately (NotRequired restart).
    Memory changes may restart the container (RestartContainer policy).

    Also patches the parent Deployment/StatefulSet to prevent controller revert.
    """
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Validate at least one resource is being changed
    if not any([
        resize_request.cpu_request,
        resize_request.cpu_limit,
        resize_request.memory_request,
        resize_request.memory_limit,
    ]):
        raise HTTPException(
            status_code=400,
            detail="At least one resource field must be specified",
        )

    k8s_manager = K8sServiceManager()
    success, error, details = k8s_manager.resize_pod_resources(
        namespace=service.namespace,
        pod_name=pod_name,
        container_name=container_name,
        cpu_request=resize_request.cpu_request,
        cpu_limit=resize_request.cpu_limit,
        memory_request=resize_request.memory_request,
        memory_limit=resize_request.memory_limit,
    )

    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resize pod resources: {error}",
        )

    # Log the action
    action = ServiceAction(
        service_id=service.id,
        action="resize_resources",
        performed_by=current_user.get("preferred_username") or "unknown",
        details=f"Pod={pod_name}, Container={container_name}, "
                f"CPU={resize_request.cpu_request or '-'}/{resize_request.cpu_limit or '-'}, "
                f"Mem={resize_request.memory_request or '-'}/{resize_request.memory_limit or '-'}",
    )
    db.add(action)
    db.commit()

    return PodResourceResizeResponse(
        success=True,
        message=f"Resources resized for {pod_name}/{container_name}",
        previous_resources=details.get("previous_resources") if details else None,
        new_resources=details.get("new_resources") if details else None,
        resize_status=details.get("resize_status") if details else None,
    )
