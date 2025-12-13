"""
SQLAlchemy model for JupyterHub configuration

This model stores default resource allocations for JupyterHub spawner.
Maximum limits are calculated dynamically from cluster resources.

Note: Image selection was removed - we now use a fixed tk-jupyter-base image
with venvs providing different Python environments via kernel selection.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from uuid import uuid4

from app.db.session import Base


class JupyterHubConfig(Base):
    """Configuration for JupyterHub defaults

    This is a single-row configuration table that stores the default
    node selection and resource allocations.

    Note: Image is fixed to tk-jupyter-base. Users select Python
    environments via Jupyter kernel dropdown (ml-gpu, fine-tuning, agent-dev).
    """

    __tablename__ = "jupyterhub_config"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Node configuration
    default_node = Column(String, nullable=True)

    # Default resource allocations (what's pre-selected in dropdowns)
    default_cpu_cores = Column(Integer, nullable=False, default=4)
    default_memory_gb = Column(Integer, nullable=False, default=8)
    default_gpu_count = Column(Integer, nullable=False, default=0)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    def to_dict(self):
        """Convert model to dictionary for API responses"""
        return {
            "id": str(self.id),
            "default_node": self.default_node,
            "default_cpu_cores": self.default_cpu_cores,
            "default_memory_gb": self.default_memory_gb,
            "default_gpu_count": self.default_gpu_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return (
            f"<JupyterHubConfig("
            f"node={self.default_node}, "
            f"cpu={self.default_cpu_cores}, "
            f"mem={self.default_memory_gb}GB, "
            f"gpu={self.default_gpu_count})>"
        )