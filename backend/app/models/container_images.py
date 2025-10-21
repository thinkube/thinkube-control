"""SQLAlchemy models for container image management"""

from datetime import datetime
from typing import Optional, Dict, Any
from uuid import uuid4

from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    Text,
    JSON,
    CheckConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class ContainerImage(Base):
    """Model for tracking container images in Harbor registry"""

    __tablename__ = "container_images"
    __table_args__ = (
        CheckConstraint(
            "category IN ('system', 'user')",
            name="check_image_category"
        ),
        UniqueConstraint(
            'registry', 'repository', 'tag',
            name='unique_image_per_registry'
        ),
        Index('idx_image_category', 'category'),
        Index('idx_image_protected', 'protected'),
        Index('idx_image_name', 'name'),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Basic image information
    name = Column(String(255), nullable=False, index=True)  # Image name without registry
    registry = Column(String(255), nullable=False)  # Registry hostname
    repository = Column(String(500), nullable=False)  # Full repository path
    tag = Column(String(128), nullable=False, default="latest")  # Image tag

    # Source and destination URLs
    source_url = Column(Text, nullable=True)  # Original source (e.g., docker.io/library/alpine:latest)
    destination_url = Column(Text, nullable=False)  # Full Harbor URL

    # Metadata
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=False, index=True)  # system or user
    protected = Column(Boolean, nullable=False, default=False)  # Prevent deletion

    # New fields for better organization
    source = Column(String(50), nullable=False, default="mirrored")  # playbook/mirrored/built
    is_base = Column(Boolean, default=False, nullable=False)  # Can be used as base image
    template = Column(Text, nullable=True)  # Dockerfile template if is_base=True

    # Timestamps
    mirror_date = Column(DateTime(timezone=True), nullable=False)  # When mirrored/built
    last_synced = Column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp()
    )  # Last sync with Harbor
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False
    )

    # Additional metadata (JSON field for flexibility)
    image_metadata = Column(JSON, nullable=True, default={})
    # Can include: base_image, packages, ml_frameworks, size, layers, etc.

    # Harbor-specific information
    harbor_project = Column(String(255), nullable=True, default="library")
    digest = Column(String(255), nullable=True)  # Image digest from Harbor
    size_bytes = Column(String(50), nullable=True)  # Image size
    vulnerabilities = Column(JSON, nullable=True, default={})  # Vulnerability scan results

    # Usage tracking
    usage_count = Column(JSON, nullable=True, default={})  # Track which deployments use this image
    last_pulled = Column(DateTime(timezone=True), nullable=True)  # Last pull time from Harbor

    def __repr__(self):
        return f"<ContainerImage(name={self.name}, tag={self.tag}, category={self.category})>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary for API responses"""
        return {
            "id": str(self.id),
            "name": self.name,
            "registry": self.registry,
            "repository": self.repository,
            "tag": self.tag,
            "source_url": self.source_url,
            "destination_url": self.destination_url,
            "description": self.description,
            "category": self.category,
            "protected": self.protected,
            "is_base": self.is_base,
            "template": self.template,
            "mirror_date": self.mirror_date.isoformat() if self.mirror_date else None,
            "last_synced": self.last_synced.isoformat() if self.last_synced else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "metadata": self.image_metadata or {},
            "harbor_project": self.harbor_project,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "vulnerabilities": self.vulnerabilities or {},
            "usage_count": self.usage_count or {},
            "last_pulled": self.last_pulled.isoformat() if self.last_pulled else None,
        }

    @property
    def full_image_name(self) -> str:
        """Get the full image name including registry, repository, and tag"""
        return f"{self.registry}/{self.repository}:{self.tag}"

    @property
    def is_vulnerable(self) -> bool:
        """Check if image has known vulnerabilities"""
        if not self.vulnerabilities:
            return False
        return any(
            self.vulnerabilities.get(severity, 0) > 0
            for severity in ["critical", "high"]
        )

    @property
    def vulnerability_summary(self) -> Dict[str, int]:
        """Get vulnerability count by severity"""
        return {
            "critical": self.vulnerabilities.get("critical", 0) if self.vulnerabilities else 0,
            "high": self.vulnerabilities.get("high", 0) if self.vulnerabilities else 0,
            "medium": self.vulnerabilities.get("medium", 0) if self.vulnerabilities else 0,
            "low": self.vulnerabilities.get("low", 0) if self.vulnerabilities else 0,
        }


class ImageMirrorJob(Base):
    """Model for tracking image mirroring jobs"""

    __tablename__ = "image_mirror_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            name="check_job_status"
        ),
        Index('idx_job_status', 'status'),
        Index('idx_job_created', 'created_at'),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Job information
    job_type = Column(String(50), nullable=False)  # mirror, build, scan
    status = Column(String(50), nullable=False, default="pending")

    # Image information
    image_id = Column(UUID(as_uuid=True), nullable=True)  # Link to ContainerImage
    source_url = Column(Text, nullable=False)
    destination_url = Column(Text, nullable=True)
    image_category = Column(String(50), nullable=False, default="user")

    # Execution details
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    logs = Column(Text, nullable=True)

    # User information
    created_by = Column(String(255), nullable=True)  # Username who initiated

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        nullable=False
    )

    # Additional metadata
    job_metadata = Column(JSON, nullable=True, default={})

    def __repr__(self):
        return f"<ImageMirrorJob(id={self.id}, status={self.status}, type={self.job_type})>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary for API responses"""
        return {
            "id": str(self.id),
            "image_id": str(self.image_id) if self.image_id else None,
            "job_type": self.job_type,
            "status": self.status,
            "source_url": self.source_url,
            "destination_url": self.destination_url,
            "image_category": self.image_category,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "logs": self.logs,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": self.job_metadata or {},
        }