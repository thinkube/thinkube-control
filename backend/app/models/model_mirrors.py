# Copyright 2025 Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
Model for tracking HuggingFace model mirror jobs
"""

from datetime import datetime
from typing import Optional, Dict, Any
from uuid import uuid4

from sqlalchemy import (
    Column, String, DateTime, Text,
    CheckConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.session import Base


class ModelMirrorJob(Base):
    """Model for tracking HuggingFace to MLflow model mirror jobs"""

    __tablename__ = "model_mirror_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="check_mirror_status"
        ),
        Index('idx_mirror_status', 'status'),
        Index('idx_mirror_model_id', 'model_id'),
        Index('idx_mirror_created', 'created_at'),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Model information - unique constraint ensures one active job per model
    model_id = Column(String(255), nullable=False, unique=True, index=True)

    # Job status
    status = Column(String(50), nullable=False, default="pending", index=True)

    # Workflow tracking
    workflow_name = Column(String(255), nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Timestamps
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

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary for API responses"""
        return {
            "id": str(self.id),
            "model_id": self.model_id,
            "status": self.status,
            "workflow_name": self.workflow_name,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_running": self.status in ['pending', 'running'],
            "is_complete": self.status == 'succeeded',
            "is_failed": self.status in ['failed', 'cancelled'],
        }
