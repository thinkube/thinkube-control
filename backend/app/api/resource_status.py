"""Resource status endpoint for disabled services"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

from app.db.session import get_db
from app.models.services import Service as ServiceModel
from app.services.k8s_manager import K8sServiceManager
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Set up templates directory
templates_dir = Path(__file__).parent.parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))


@router.get("/{service_name}", response_class=HTMLResponse)
async def get_resource_status(
    request: Request,
    service_name: str,
    db: Session = Depends(get_db)
):
    """Return resource optimization status page for disabled services"""
    
    # Get service info from database
    service = db.query(ServiceModel).filter(
        ServiceModel.name == service_name
    ).first()
    
    # Initialize K8s manager to get resource usage
    gpu_users = []
    active_services = []
    resource_summary = "Loading resource information..."
    
    try:
        k8s_manager = K8sServiceManager()
        
        # Get all services to check GPU usage
        all_services = db.query(ServiceModel).filter(
            ServiceModel.is_enabled == True
        ).all()
        
        # Check GPU usage for each enabled service
        for svc in all_services:
            if svc.namespace:
                gpu_info = k8s_manager.get_namespace_gpu_usage(svc.namespace)
                if gpu_info and gpu_info.get("total_gpus", 0) > 0:
                    gpu_users.append({
                        'name': svc.display_name,
                        'gpu_count': gpu_info.get("total_gpus"),
                        'nodes': gpu_info.get("gpu_nodes", [])
                    })
                active_services.append(svc.display_name)
        
        # Create resource summary
        gpu_count = sum(u['gpu_count'] for u in gpu_users)
        cpu_count = len([s for s in active_services if s not in [g['name'] for g in gpu_users]])
        
        if gpu_count > 0 and cpu_count > 0:
            resource_summary = f"{gpu_count} GPU{'s' if gpu_count > 1 else ''} and {cpu_count} CPU service{'s' if cpu_count > 1 else ''} active"
        elif gpu_count > 0:
            resource_summary = f"{gpu_count} GPU service{'s' if gpu_count > 1 else ''} active"
        elif cpu_count > 0:
            resource_summary = f"{cpu_count} CPU service{'s' if cpu_count > 1 else ''} active"
        else:
            resource_summary = "No services currently active"
            
    except Exception as e:
        logger.error(f"Error getting resource status: {e}")
        resource_summary = "Resource information unavailable"
    
    # Check if this is a GPU service
    gpu_service = False
    if service and service.service_metadata:
        # Check if service metadata indicates GPU usage
        metadata_str = str(service.service_metadata).lower()
        gpu_service = 'gpu' in metadata_str or 'nvidia' in metadata_str
    
    # Format GPU users list
    gpu_users_str = ""
    if gpu_users:
        gpu_users_list = [f"{u['name']} ({u['gpu_count']} GPU{'s' if u['gpu_count'] > 1 else ''})" 
                         for u in gpu_users]
        gpu_users_str = ", ".join(gpu_users_list)
    
    return templates.TemplateResponse("resource_status.html", {
        "request": request,
        "service_name": service_name,
        "service_display_name": service.display_name if service else service_name.title(),
        "service_description": service.description if service else "",
        "gpu_service": gpu_service,
        "current_gpu_users": gpu_users_str if gpu_users_str else "No GPU resources in use",
        "resource_summary": resource_summary,
        "domain": settings.DOMAIN_NAME,
        "control_subdomain": "control"
    })


# 🤖 Generated with [Claude Code](https://claude.ai/code)