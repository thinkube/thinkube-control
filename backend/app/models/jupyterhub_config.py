"""
SQLAlchemy model for JupyterHub configuration

This model stores default resource allocations for JupyterHub spawner.
Maximum limits are calculated dynamically from cluster resources.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func
from uuid import uuid4

from app.db.session import Base


class JupyterHubConfig(Base):
    """Configuration for JupyterHub defaults

    This is a single-row configuration table that stores the default
    image selection, node selection, and resource allocations.
    """

    __tablename__ = "jupyterhub_config"

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Image configuration
    hidden_images = Column(ARRAY(String), nullable=False, default=list, server_default='{}')
    default_image = Column(String, nullable=False, default='tk-jupyter-ml-cpu')

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
            "hidden_images": self.hidden_images or [],
            "default_image": self.default_image,
            "default_node": self.default_node,
            "default_cpu_cores": self.default_cpu_cores,
            "default_memory_gb": self.default_memory_gb,
            "default_gpu_count": self.default_gpu_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return (
            f"<JupyterHubConfig(image={self.default_image}, "
            f"node={self.default_node}, "
            f"cpu={self.default_cpu_cores}, "
            f"mem={self.default_memory_gb}GB, "
            f"gpu={self.default_gpu_count})>"
        )