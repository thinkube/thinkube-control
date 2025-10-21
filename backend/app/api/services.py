"""Service management API endpoints"""

import logging
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.db.session import get_db
from app.models.services import Service as ServiceModel, ServiceHealth, ServiceAction
from app.models.service_schemas import (
    Service as ServiceSchema,
    ServiceList,
    ServiceListMinimal,
    ServiceMinimal,
    ServiceDetail,
    ServiceUpdate,
    ServiceToggle,
    ServiceNameCheck,
    ServiceNameCheckResponse,
    ServiceHealthHistory,
    ServiceDependencyInfo,
    ServiceStatusUpdate,
    ServiceStateChange,
    ServiceType,
    ServiceActionResponse,
)
from app.services import (
    ServiceDiscovery,
    K8sServiceManager,
    DependencyManager,
    health_checker,
)
from app.core.api_tokens import get_current_user_dual_auth


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=ServiceList, operation_id="list_services")
async def list_services(
    service_type: Optional[ServiceType] = Query(
        None, description="Filter by service type"
    ),
    category: Optional[str] = Query(None, description="Filter by category"),
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """List all services with optional filters"""
    from app.models.favorites import UserFavorite

    query = db.query(ServiceModel)

    # Apply filters
    if service_type:
        query = query.filter(ServiceModel.type == service_type)
    if category:
        query = query.filter(ServiceModel.category == category)
    if enabled is not None:
        query = query.filter(ServiceModel.is_enabled == enabled)

    # Order by type (core first) and name
    services = query.order_by(ServiceModel.type, ServiceModel.name).all()

    # Get user's favorites
    user_id = current_user.get("sub")
    user_favorites = set()
    favorites_order = {}
    if user_id:
        favorites = db.query(UserFavorite).filter(UserFavorite.user_id == user_id).all()
        user_favorites = set(str(fav.service_id) for fav in favorites)
        favorites_order = {
            str(fav.service_id): fav.order_index or 0 for fav in favorites
        }

    # Initialize K8s manager for GPU info
    k8s_manager = None
    try:
        k8s_manager = K8sServiceManager()
    except Exception as e:
        logger.warning(f"Could not initialize K8s manager: {e}")

    # Convert to Pydantic schemas with latest health status
    service_responses = []
    for service in services:
        # Get latest health record if exists
        latest_health = None
        if service.health_records:
            latest_health = max(service.health_records, key=lambda h: h.checked_at)

        # Create ServiceSchema instance with computed fields
        service_schema = ServiceSchema.model_validate(service)
        service_schema.latest_health = latest_health
        service_schema.can_be_disabled = service.type in ["optional", "user_app"]
        service_schema.is_favorite = str(service.id) in user_favorites
        
        # Get GPU info if this is a deployed application
        if k8s_manager and service.namespace:
            try:
                # Get all deployments in the namespace and check GPU usage
                gpu_info = k8s_manager.get_namespace_gpu_usage(service.namespace)
                if gpu_info:
                    service_schema.gpu_count = gpu_info.get("total_gpus", 0)
                    service_schema.gpu_nodes = gpu_info.get("gpu_nodes", [])
            except Exception as e:
                logger.debug(f"Could not get GPU info for {service.name}: {e}")
        
        service_responses.append(service_schema)

    return ServiceList(services=service_responses, total=len(service_responses))


@router.get("/minimal", response_model=ServiceListMinimal, operation_id="list_services_minimal")
async def list_services_minimal(
    service_type: Optional[ServiceType] = Query(
        None, description="Filter by service type"
    ),
    category: Optional[str] = Query(None, description="Filter by category"),
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """List all services with minimal data (optimized for MCP)"""
    query = db.query(ServiceModel)

    # Apply filters
    filters = []
    if service_type:
        filters.append(ServiceModel.type == service_type)
    if category:
        filters.append(ServiceModel.category == category)
    if enabled is not None:
        filters.append(ServiceModel.is_enabled == enabled)

    if filters:
        query = query.filter(and_(*filters))

    services = query.all()

    # Convert to minimal schemas
    minimal_services = [
        ServiceMinimal(
            id=service.id,
            name=service.name,
            display_name=service.display_name,
            type=service.type,
            is_enabled=service.is_enabled,
            category=service.category,
        )
        for service in services
    ]

    return ServiceListMinimal(services=minimal_services, total=len(minimal_services))


@router.get(
    "/{service_id}", response_model=ServiceDetail, operation_id="get_service_details"
)
async def get_service_details(
    service_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get detailed information about a specific service"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Don't include full health history here - use the /health endpoint instead
    # Just get the latest health status
    latest_health = (
        db.query(ServiceHealth)
        .filter(ServiceHealth.service_id == service_id)
        .order_by(ServiceHealth.checked_at.desc())
        .first()
    )

    # Get recent actions
    recent_actions = (
        db.query(ServiceAction)
        .filter(ServiceAction.service_id == service_id)
        .order_by(ServiceAction.performed_at.desc())
        .limit(10)
        .all()
    )

    # Get Kubernetes status
    k8s_manager = K8sServiceManager()
    k8s_status = k8s_manager.get_deployment_status(service.namespace, service.name)

    # Build response
    service_detail = ServiceDetail(
        **service.__dict__,
        latest_health=latest_health,
        can_be_disabled=service.type in ["optional", "user_app"],
        health_history=[],  # Empty - use /health endpoint for full history
        recent_actions=recent_actions,
        resource_usage=k8s_status.get("resource_usage") if k8s_status else None,
        pods_info=k8s_status.get("pods") if k8s_status else None,
    )

    return service_detail


@router.patch("/{service_id}", response_model=ServiceDetail)
async def update_service(
    service_id: UUID,
    update_data: ServiceUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Update service information (metadata only, not state)"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Update fields
    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(service, field, value)

    db.commit()
    db.refresh(service)

    return await get_service_details(service_id, db, current_user)


@router.post(
    "/{service_id}/toggle", response_model=ServiceDetail, operation_id="toggle_service"
)
async def toggle_service(
    service_id: UUID,
    toggle_data: ServiceToggle,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Enable or disable a service"""
    # No admin check - all authenticated users can toggle optional/user_app services
    
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if service.type == "core":
        raise HTTPException(status_code=400, detail="Core services cannot be disabled")

    # Check dependencies
    dep_manager = DependencyManager(db)

    if toggle_data.is_enabled:
        # Enabling service
        can_enable, error_msg, disabled_deps = dep_manager.validate_enable_action(
            service
        )
        if not can_enable:
            raise HTTPException(status_code=400, detail=error_msg)
    else:
        # Disabling service
        can_disable, warning_msg, affected_services = (
            dep_manager.validate_disable_action(service)
        )
        if not can_disable:
            raise HTTPException(status_code=400, detail=warning_msg)

        # If there's a warning, include it in the response
        if warning_msg:
            # You might want to return this as a warning in the response
            logger.warning(warning_msg)

    # Perform the action
    k8s_manager = K8sServiceManager()
    if toggle_data.is_enabled:
        success, error = k8s_manager.enable_service(service)
    else:
        success, error = k8s_manager.disable_service(service)

    if not success:
        raise HTTPException(
            status_code=500, detail=f"Failed to toggle service: {error}"
        )

    # Update service state
    service.is_enabled = toggle_data.is_enabled

    # Log the action
    action = ServiceAction(
        service_id=service.id,
        action="enable" if toggle_data.is_enabled else "disable",
        performed_by=current_user.get("preferred_username") or "unknown",
        details={"reason": toggle_data.reason} if toggle_data.reason else {},
    )
    db.add(action)

    db.commit()
    db.refresh(service)

    # Trigger a health check in the background
    if toggle_data.is_enabled:
        background_tasks.add_task(health_checker.check_single_service, str(service.id))

    return await get_service_details(service_id, db, current_user)


@router.post("/{service_id}/restart", operation_id="restart_service")
async def restart_service(
    service_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Restart a service"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if not service.is_enabled:
        raise HTTPException(status_code=400, detail="Cannot restart a disabled service")

    # Restart the service
    k8s_manager = K8sServiceManager()
    success, error = k8s_manager.restart_deployment(service.namespace, service.name)

    if not success:
        raise HTTPException(
            status_code=500, detail=f"Failed to restart service: {error}"
        )

    # Log the action
    action = ServiceAction(
        service_id=service.id,
        action="restart",
        performed_by=current_user.get("preferred_username") or "unknown",
    )
    db.add(action)
    db.commit()

    return {"message": f"Service {service.display_name} restarted successfully"}


@router.get("/{service_id}/health")  # No response_model - return plain dict
async def get_service_health_history(
    service_id: UUID,
    hours: int = Query(24, ge=1, le=168, description="Number of hours of history"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get health history for a service"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Get health history and return as plain dict (no Pydantic validation)
    return await health_checker.get_service_health_history(
        str(service_id), hours
    )


@router.post("/{service_id}/health-check")
async def trigger_health_check(
    service_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Trigger a manual health check for a service"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Perform health check
    result = await health_checker.check_single_service(str(service_id))

    return result


@router.get("/{service_id}/dependencies", response_model=ServiceDependencyInfo)
async def get_service_dependencies(
    service_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get dependency information for a service"""
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    dep_manager = DependencyManager(db)

    # Get dependencies
    dependencies = dep_manager.check_dependencies(service)

    # Get dependents
    dependents = dep_manager.get_dependents(service)
    dependents_info = [
        {
            "id": str(dep.id),
            "name": dep.name,
            "display_name": dep.display_name,
            "type": dep.type,
            "enabled": dep.is_enabled,
        }
        for dep in dependents
    ]

    # Check if can disable
    can_disable, warning, _ = dep_manager.validate_disable_action(service)

    return ServiceDependencyInfo(
        service_id=service.id,
        name=service.name,
        dependencies=dependencies,
        dependents=dependents_info,
        can_disable=can_disable,
        disable_warning=warning,
    )


@router.post("/check-name", response_model=ServiceNameCheckResponse)
async def check_service_name(
    name_check: ServiceNameCheck,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Check if a service name is available"""
    dep_manager = DependencyManager(db)

    is_valid, message = dep_manager.validate_service_name(
        name_check.name, name_check.type
    )

    response = ServiceNameCheckResponse(available=is_valid, reason=message)

    # If name exists, provide info about existing service
    if not is_valid and "already in use" in (message or ""):
        existing = (
            db.query(ServiceModel).filter(ServiceModel.name == name_check.name).first()
        )
        if existing:
            response.existing_service = {
                "id": str(existing.id),
                "type": existing.type,
                "display_name": existing.display_name,
            }

    return response


@router.post("/sync")
async def sync_services(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Manually trigger service discovery and sync"""
    # Any authenticated user can sync services
    # No special role required since they can already deploy services

    try:
        # Get domain from environment
        import os

        domain = os.getenv("DOMAIN_NAME", "thinkube.com")

        # Run service discovery
        discovery = ServiceDiscovery(db, domain)
        discovered = discovery.discover_all()

        # Build response
        result = {}
        for service_type, services in discovered.items():
            result[service_type] = [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "enabled": s.is_enabled,
                }
                for s in services
            ]

        return {"message": "Service sync completed successfully", "discovered": result}

    except Exception as e:
        logger.error(f"Service sync failed: {e}")
        raise HTTPException(status_code=500, detail=f"Service sync failed: {str(e)}")


# Favorites endpoints
@router.get("/favorites", response_model=ServiceList)
async def get_favorite_services(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get user's favorite services"""
    from app.models.favorites import UserFavorite

    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found")

    # Get favorite services ordered by order_index
    favorites = (
        db.query(ServiceModel, UserFavorite.order_index)
        .join(UserFavorite, ServiceModel.id == UserFavorite.service_id)
        .filter(UserFavorite.user_id == user_id)
        .order_by(UserFavorite.order_index.nullslast())
        .all()
    )

    # Convert to response schemas
    services = []
    for service, order_index in favorites:
        service_schema = ServiceSchema.model_validate(service)
        service_schema.is_favorite = True
        services.append(service_schema)

    return ServiceList(
        services=services, total=len(services), filters={"favorites_only": True}
    )


@router.post("/{service_id}/favorite", response_model=ServiceSchema)
async def add_to_favorites(
    service_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Add a service to user's favorites"""
    from app.models.favorites import UserFavorite

    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found")

    # Check if service exists
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # Check if already favorited
    existing = (
        db.query(UserFavorite)
        .filter(UserFavorite.user_id == user_id, UserFavorite.service_id == service_id)
        .first()
    )

    if existing:
        raise HTTPException(status_code=400, detail="Service already in favorites")

    # Get max order index for user
    max_order = (
        db.query(func.max(UserFavorite.order_index))
        .filter(UserFavorite.user_id == user_id)
        .scalar()
        or 0
    )

    # Add to favorites
    favorite = UserFavorite(
        user_id=user_id, service_id=service_id, order_index=max_order + 1
    )
    db.add(favorite)
    db.commit()

    # Return service with is_favorite=True
    service_schema = ServiceSchema.model_validate(service)
    service_schema.is_favorite = True
    return service_schema


@router.delete("/{service_id}/favorite")
async def remove_from_favorites(
    service_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Remove a service from user's favorites"""
    from app.models.favorites import UserFavorite

    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found")

    # Find and delete favorite
    favorite = (
        db.query(UserFavorite)
        .filter(UserFavorite.user_id == user_id, UserFavorite.service_id == service_id)
        .first()
    )

    if not favorite:
        raise HTTPException(status_code=404, detail="Service not in favorites")

    db.delete(favorite)
    db.commit()

    return {"message": "Service removed from favorites"}


@router.put("/favorites/reorder")
async def reorder_favorites(
    service_ids: List[UUID],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Reorder favorite services"""
    from app.models.favorites import UserFavorite

    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found")

    # Get all user's favorites
    favorites = db.query(UserFavorite).filter(UserFavorite.user_id == user_id).all()

    # Create a map of service_id to favorite
    favorites_map = {str(fav.service_id): fav for fav in favorites}

    # Update order based on the provided list
    for index, service_id in enumerate(service_ids):
        service_id_str = str(service_id)
        if service_id_str in favorites_map:
            favorites_map[service_id_str].order_index = index

    db.commit()

    return {"message": "Favorites reordered successfully"}


@router.get("/{service_id}/pods/{pod_name}/describe")
async def describe_pod(
    service_id: UUID,
    pod_name: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get detailed pod description"""
    # Get service to find namespace
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    k8s_manager = K8sServiceManager()
    pod_info = k8s_manager.describe_pod(service.namespace, pod_name)

    if not pod_info:
        raise HTTPException(status_code=404, detail="Pod not found")

    return pod_info


@router.get("/{service_id}/pods/{pod_name}/containers/{container_name}/logs")
async def get_container_logs(
    service_id: UUID,
    pod_name: str,
    container_name: str,
    lines: int = 500,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get container logs"""
    # Get service to find namespace
    service = db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    k8s_manager = K8sServiceManager()
    logs = k8s_manager.get_container_logs(
        service.namespace, pod_name, container_name, lines
    )

    return {"logs": logs, "lines": lines}


# 🤖 Generated with [Claude Code](https://claude.ai/code)
