# app/models/cicd.py
"""Database models for CI/CD monitoring."""

import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import (
    Column,
    String,
    DateTime,
    Text,
    Integer,
    JSON,
    ForeignKey,
    Numeric,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.db.session import Base


class PipelineStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageStatus(str, enum.Enum):
    """Status for pipeline stages/tasks"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Pipeline(Base):
    """Main pipeline tracking table."""

    __tablename__ = "pipelines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_name = Column(String(255), nullable=False, index=True)
    branch = Column(String(255), nullable=False)
    commit_sha = Column(String(40), nullable=False)
    commit_message = Column(Text)
    author_email = Column(String(255))
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)
    status = Column(
        SQLEnum(PipelineStatus),
        nullable=False,
        default=PipelineStatus.RUNNING,
        index=True,
    )
    trigger_type = Column(String(50))  # git_push, manual, scheduled
    workflow_uid = Column(
        String(100), index=True
    )  # Argo Workflow UID for reliable tracking
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    stages = relationship(
        "PipelineStage", back_populates="pipeline", cascade="all, delete-orphan"
    )
    metrics = relationship(
        "PipelineMetric", back_populates="pipeline", cascade="all, delete-orphan"
    )


class PipelineStage(Base):
    """Pipeline stages/tasks that have state and duration."""

    __tablename__ = "pipeline_stages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_name = Column(
        String(100), nullable=False
    )  # e.g., "backend_tests", "frontend_build"
    component = Column(
        String(100), nullable=False
    )  # e.g., "backend", "frontend", "workflow"
    status = Column(
        SQLEnum(StageStatus), nullable=False, default=StageStatus.PENDING, index=True
    )
    started_at = Column(DateTime, nullable=True)  # Set when stage actually starts
    completed_at = Column(DateTime)
    error_message = Column(Text)
    details = Column(JSON, nullable=False, default={})
    retry_count = Column(Integer, default=0)
    parent_stage_id = Column(UUID(as_uuid=True), ForeignKey("pipeline_stages.id"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    pipeline = relationship("Pipeline", back_populates="stages")
    parent_stage = relationship("PipelineStage", remote_side=[id])


# Event models removed - using stage-based approach only


class PipelineMetric(Base):
    """Metrics for performance tracking."""

    __tablename__ = "pipeline_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Numeric, nullable=False)
    unit = Column(String(50))
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    pipeline = relationship("Pipeline", back_populates="metrics")


# 🤖 Generated with Claude
