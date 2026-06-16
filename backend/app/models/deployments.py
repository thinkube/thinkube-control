"""
Database models for template deployments
Tracks deployment history and logs for async execution
"""

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.session import Base


class TemplateDeployment(Base):
    """Track template deployments"""

    __tablename__ = "template_deployments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    template_url = Column(Text, nullable=False)
    status = Column(
        String(50), nullable=False, default="pending"
    )  # pending, running, success, failed, cancelled
    variables = Column(JSON, nullable=True)  # Template variables used
    output = Column(Text, nullable=True)  # Final output/summary

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # User tracking
    created_by = Column(String(255), nullable=False)

    # Relationships
    logs = relationship(
        "DeploymentLog", back_populates="deployment", cascade="all, delete-orphan"
    )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": str(self.id),
            "name": self.name,
            "template_url": self.template_url,
            "status": self.status,
            "variables": self.variables,
            "output": self.output,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "created_by": self.created_by,
            "duration": self._calculate_duration(),
        }

    def _calculate_duration(self):
        """Calculate deployment duration in seconds"""
        if not self.started_at:
            return None

        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        else:
            # Still running - use UTC to match database timestamps
            from datetime import datetime, timezone

            return (datetime.now(timezone.utc) - self.started_at).total_seconds()


class DeploymentLog(Base):
    """Track deployment logs for streaming and history"""

    __tablename__ = "deployment_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id = Column(
        UUID(as_uuid=True), ForeignKey("template_deployments.id"), nullable=False
    )

    # Log data
    timestamp = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    type = Column(
        String(50), nullable=False
    )  # task, play, ok, changed, failed, skipped, output, error
    message = Column(Text, nullable=False)
    task_name = Column(String(255), nullable=True)
    task_number = Column(Integer, nullable=True)

    # Relationships
    deployment = relationship("TemplateDeployment", back_populates="logs")

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": str(self.id),
            "deployment_id": str(self.deployment_id),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "type": self.type,
            "message": self.message,
            "task_name": self.task_name,
            "task_number": self.task_number,
        }
