# app/db/base.py
"""Import all models to register them with SQLAlchemy."""

# Import Base first
from app.db.session import Base

# Import all models to register them
from app.models import (
    Pipeline,
    PipelineStage,
    Service,
    ServiceHealth,
    ServiceAction,
    TemplateDeployment,
    DeploymentLog,
    JupyterHubConfig,
)
from app.models.secrets import Secret, AppSecret

# This ensures all models are registered with the Base metadata
__all__ = ["Base"]
