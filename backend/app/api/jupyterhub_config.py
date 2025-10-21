"""API endpoints for JupyterHub configuration management"""

import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.jupyterhub_config import JupyterHubConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyterhub", tags=["jupyterhub-config"])


# Pydantic models for request/response
class JupyterHubConfigUpdate(BaseModel):
    """Request model for updating JupyterHub configuration"""
    hidden_images: List[str] = Field(default_factory=list, description="Images to hide from JupyterHub")
    default_image: str = Field(description="Default image to pre-select")
    default_node: Optional[str] = Field(None, description="Default node to pre-select")
    default_cpu_cores: int = Field(ge=1, le=128, description="Default CPU cores")
    default_memory_gb: int = Field(ge=1, le=512, description="Default memory in GB")
    default_gpu_count: int = Field(ge=0, le=8, description="Default GPU count")


class JupyterHubConfigResponse(BaseModel):
    """Response model for JupyterHub configuration"""
    id: str
    hidden_images: List[str]
    default_image: str
    default_node: Optional[str]
    default_cpu_cores: int
    default_memory_gb: int
    default_gpu_count: int
    created_at: str
    updated_at: str


@router.get("/config", response_model=JupyterHubConfigResponse, operation_id="get_jupyterhub_config")
def get_jupyterhub_config(
    db: Session = Depends(get_db)
):
    """Get JupyterHub configuration

    This endpoint is called by JupyterHub to get default resource allocations.
    No authentication required as it's called from within the cluster.

    Returns the current configuration or creates a default one if none exists.
    """
    try:
        # Get or create configuration (single row table)
        config = db.query(JupyterHubConfig).first()

        if not config:
            logger.info("No JupyterHub configuration found, creating default")
            config = JupyterHubConfig(
                hidden_images=[],
                default_image='tk-jupyter-ml-cpu',
                default_node=None,
                default_cpu_cores=4,
                default_memory_gb=8,
                default_gpu_count=0
            )
            db.add(config)
            db.commit()
            db.refresh(config)

        return JupyterHubConfigResponse(**config.to_dict())

    except Exception as e:
        logger.error(f"Error getting JupyterHub configuration: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get JupyterHub configuration: {str(e)}"
        )


@router.put("/config", response_model=JupyterHubConfigResponse, operation_id="update_jupyterhub_config")
def update_jupyterhub_config(
    config_update: JupyterHubConfigUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Update JupyterHub configuration (admin only)

    Updates the default image, node, and resource allocations.
    Requires authentication.
    """
    try:

        # Get or create configuration
        config = db.query(JupyterHubConfig).first()

        if not config:
            logger.info("Creating new JupyterHub configuration")
            config = JupyterHubConfig()
            db.add(config)

        # Update fields
        config.hidden_images = config_update.hidden_images
        config.default_image = config_update.default_image
        config.default_node = config_update.default_node
        config.default_cpu_cores = config_update.default_cpu_cores
        config.default_memory_gb = config_update.default_memory_gb
        config.default_gpu_count = config_update.default_gpu_count

        db.commit()
        db.refresh(config)

        logger.info(
            f"JupyterHub configuration updated by {current_user.get('preferred_username', 'unknown')}: "
            f"image={config.default_image}, node={config.default_node}, "
            f"defaults=({config.default_cpu_cores}CPU, {config.default_memory_gb}GB, {config.default_gpu_count}GPU), "
            f"hidden_images={len(config.hidden_images)}"
        )

        return JupyterHubConfigResponse(**config.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating JupyterHub configuration: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update JupyterHub configuration: {str(e)}"
        )