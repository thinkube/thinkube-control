"""API endpoints for JupyterHub configuration management

Note: Image selection was removed - we now use a fixed tk-jupyter-base image
with venvs providing different Python environments via kernel selection.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel, Field

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.jupyterhub_config import JupyterHubConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyterhub", tags=["jupyterhub-config"])


# Pydantic models for request/response
class JupyterHubConfigUpdate(BaseModel):
    """Request model for updating JupyterHub configuration"""
    default_node: Optional[str] = Field(None, description="Default node to pre-select")
    default_cpu_cores: int = Field(ge=1, le=128, description="Default CPU cores")
    default_memory_gb: int = Field(ge=1, le=512, description="Default memory in GB")
    default_gpu_count: int = Field(ge=0, le=8, description="Default GPU count")


class JupyterHubConfigResponse(BaseModel):
    """Response model for JupyterHub configuration"""
    id: str
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
        config.default_node = config_update.default_node
        config.default_cpu_cores = config_update.default_cpu_cores
        config.default_memory_gb = config_update.default_memory_gb
        config.default_gpu_count = config_update.default_gpu_count

        db.commit()
        db.refresh(config)

        logger.info(
            f"JupyterHub configuration updated by {current_user.get('preferred_username', 'unknown')}: "
            f"node={config.default_node}, "
            f"defaults=({config.default_cpu_cores}CPU, {config.default_memory_gb}GB, {config.default_gpu_count}GPU)"
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