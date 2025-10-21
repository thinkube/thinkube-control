# app/api/dashboards.py
from typing import List, Optional
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.config import settings
from app.models.dashboards import DashboardItem, UserInfo
from app.core.security import get_current_active_user
from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.services import Service
from app.models.service_schemas import ServiceType
from app.services.k8s_manager import K8sServiceManager

router = APIRouter()

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@router.get("/", response_model=List[DashboardItem], operation_id="list_dashboards")
@router.get("", response_model=List[DashboardItem])
async def get_dashboards(
    request: Request,
    category: Optional[str] = Query(None, description="Filter by category"),
    enabled_only: bool = Query(True, description="Show only enabled services"),
    db: Session = Depends(get_db),
    user_data = Depends(get_current_user_dual_auth),
):
    """Return all dashboard items from the database."""
    logger.debug(f"User data received: {user_data}")

    # Build query
    query = db.query(Service)

    # Filter by category if provided
    if category:
        query = query.filter(Service.category == category)

    # Filter by enabled status
    if enabled_only:
        query = query.filter(Service.is_enabled == True)

    # Order by type (core first) and name
    services = query.order_by(Service.type, Service.name).all()

    # Initialize K8s manager for GPU info
    k8s_manager = None
    try:
        k8s_manager = K8sServiceManager()
    except Exception as e:
        logger.warning(f"Could not initialize K8s manager: {e}")

    # Convert services to dashboard items
    dashboard_items = []
    for service in services:
        # Skip services without URLs (like some infrastructure services)
        if not service.url:
            continue

        # Get GPU info if this is a deployed application
        gpu_count = None
        gpu_nodes = None
        
        if k8s_manager and service.namespace:
            try:
                # Get all deployments in the namespace and check GPU usage
                gpu_info = k8s_manager.get_namespace_gpu_usage(service.namespace)
                if gpu_info:
                    gpu_count = gpu_info.get("total_gpus", 0)
                    gpu_nodes = gpu_info.get("gpu_nodes", [])
            except Exception as e:
                logger.debug(f"Could not get GPU info for {service.name}: {e}")

        # Map service to dashboard item
        dashboard_item = DashboardItem(
            id=service.name,
            name=service.display_name,
            description=service.description or "",
            url=str(service.url),
            icon=service.icon or _get_default_icon(service.category),
            color=_get_category_color(service.category),
            category=service.category or "other",
            requires_role=None,  # Role-based access can be added later
            gpu_count=gpu_count if gpu_count and gpu_count > 0 else None,
            gpu_nodes=gpu_nodes if gpu_nodes else None,
        )
        dashboard_items.append(dashboard_item)

    return dashboard_items


@router.get("/categories")
@router.get("/categories/")
async def get_dashboard_categories(
    db: Session = Depends(get_db), user_data: dict = Depends(get_current_active_user)
):
    """Return all dashboard categories from the database."""
    # Get unique categories from services
    categories = (
        db.query(Service.category).distinct().filter(Service.category.isnot(None)).all()
    )

    category_list = sorted([cat[0] for cat in categories])

    return {"categories": category_list}


@router.get("/{dashboard_id}", response_model=DashboardItem)
async def get_dashboard(
    dashboard_id: str,
    db: Session = Depends(get_db),
    user_data = Depends(get_current_user_dual_auth),
):
    """Return a specific dashboard by ID (service name)."""
    service = db.query(Service).filter(Service.name == dashboard_id).first()

    if not service:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    if not service.url:
        raise HTTPException(status_code=404, detail="Service has no dashboard URL")

    # Convert service to dashboard item
    dashboard_item = DashboardItem(
        id=service.name,
        name=service.display_name,
        description=service.description or "",
        url=str(service.url),
        icon=service.icon or _get_default_icon(service.category),
        color=_get_category_color(service.category),
        category=service.category or "other",
        requires_role=None,
    )

    return dashboard_item


@router.get("/debug-info")
async def debug_info(request: Request):
    """Debug endpoint to check request info without authentication."""
    return {
        "headers": dict(request.headers),
        "query_params": dict(request.query_params),
        "cookies": request.cookies,
        "client": request.client,
        "url": str(request.url),
    }


def _get_default_icon(category: Optional[str]) -> str:
    """Get default icon based on category."""
    icon_map = {
        "infrastructure": "mdi-server",
        "development": "mdi-code-braces",
        "monitoring": "mdi-chart-line",
        "security": "mdi-shield-check",
        "storage": "mdi-database",
        "ai": "mdi-brain",
        "documentation": "mdi-book-open",
        "application": "mdi-application",
    }
    return icon_map.get(category, "mdi-view-dashboard")


def _get_category_color(category: Optional[str]) -> str:
    """Get color based on category."""
    color_map = {
        "infrastructure": "blue",
        "development": "green",
        "monitoring": "orange",
        "security": "red",
        "storage": "amber",
        "ai": "purple",
        "documentation": "indigo",
        "application": "teal",
    }
    return color_map.get(category, "gray")


# 🤖 Generated with [Claude Code](https://claude.ai/code)
