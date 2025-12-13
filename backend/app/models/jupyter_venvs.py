"""
SQLAlchemy models for Jupyter virtualenv management
Following the exact same pattern as CustomImageBuild
"""

from sqlalchemy import Column, String, Text, DateTime, JSON, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from app.db.session import Base


class JupyterVenv(Base):
    """Track Jupyter virtualenvs - similar to CustomImageBuild"""

    __tablename__ = "jupyter_venvs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, unique=True)  # Venv name (e.g., "my-custom-env")

    # Build configuration
    packages = Column(JSON, nullable=False)  # List of packages to install
    status = Column(
        String(50), nullable=False, default="pending"
    )  # pending, building, success, failed, cancelled
    output = Column(Text, nullable=True)  # Build log file path

    # Template/inheritance
    is_template = Column(Boolean, default=False, nullable=False)  # Is this a template (fine-tuning, agent-dev)
    parent_template_id = Column(UUID(as_uuid=True), ForeignKey('jupyter_venvs.id'), nullable=True)

    # Location
    venv_path = Column(Text, nullable=True)  # Full path on JuiceFS (e.g., /home/thinkube/venvs/custom/my-env)
    architecture = Column(String(20), nullable=True)  # arm64, amd64

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # User tracking
    created_by = Column(String(255), nullable=False)

    # Relationships
    parent_template = relationship("JupyterVenv", remote_side=[id], backref="derived_venvs")

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": str(self.id),
            "name": self.name,
            "packages": self.packages,
            "status": self.status,
            "output": self.output,
            "is_template": self.is_template,
            "parent_template_id": str(self.parent_template_id) if self.parent_template_id else None,
            "venv_path": self.venv_path,
            "architecture": self.architecture,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_by": self.created_by,
            "duration": self._calculate_duration(),
        }

    def _calculate_duration(self):
        """Calculate build duration in seconds"""
        if not self.started_at:
            return None

        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        else:
            from datetime import datetime, timezone
            return (datetime.now(timezone.utc) - self.started_at).total_seconds()
