"""
SQLAlchemy models for custom Docker image management
Following the exact same pattern as TemplateDeployment
"""

from sqlalchemy import Column, String, Text, DateTime, JSON, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from app.db.session import Base


class CustomImageBuild(Base):
    """Track custom image builds - exactly like TemplateDeployment"""

    __tablename__ = "custom_image_builds"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)  # Image name
    dockerfile_path = Column(Text, nullable=False)  # Path in shared-code/dockerfiles
    status = Column(
        String(50), nullable=False, default="pending"
    )  # pending, building, success, failed, cancelled
    build_config = Column(JSON, nullable=True)  # Build args, base image, etc.
    output = Column(Text, nullable=True)  # Final output/summary
    registry_url = Column(Text, nullable=True)  # Full URL after push to Harbor

    # New fields for templates and inheritance
    is_base = Column(Boolean, default=False, nullable=False)  # Can be used as base
    scope = Column(String(50), default="general", nullable=False)  # Image category
    parent_image_id = Column(UUID(as_uuid=True), ForeignKey('custom_image_builds.id'), nullable=True)
    template = Column(Text, nullable=True)  # Dockerfile template if is_base=True

    # Timestamps - same as TemplateDeployment
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # User tracking
    created_by = Column(String(255), nullable=False)

    # Relationships
    parent = relationship("CustomImageBuild", remote_side=[id], backref="children")

    def to_dict(self):
        """Convert to dictionary for API responses - same as TemplateDeployment"""
        return {
            "id": str(self.id),
            "name": self.name,
            "dockerfile_path": self.dockerfile_path,
            "status": self.status,
            "build_config": self.build_config,
            "output": self.output,
            "registry_url": self.registry_url,
            "is_base": self.is_base,
            "scope": self.scope,
            "parent_image_id": str(self.parent_image_id) if self.parent_image_id else None,
            "template": self.template,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "created_by": self.created_by,
            "duration": self._calculate_duration(),
        }

    def _calculate_duration(self):
        """Calculate build duration in seconds - same as TemplateDeployment"""
        if not self.started_at:
            return None

        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        else:
            # Still running - use UTC to match database timestamps
            from datetime import datetime, timezone
            return (datetime.now(timezone.utc) - self.started_at).total_seconds()