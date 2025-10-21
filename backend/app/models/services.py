"""SQLAlchemy models for service management

🤖 [AI-assisted]
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import uuid4

from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    DateTime,
    Text,
    ForeignKey,
    CheckConstraint,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, backref
from sqlalchemy.sql import func

from app.db.session import Base


class Service(Base):
    """Model for tracking all services in the Thinkube platform"""

    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint(
            "type IN ('core', 'optional', 'user_app')", name="check_service_type"
        ),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Basic information
    name = Column(
        String(255), unique=True, nullable=False, index=True
    )  # Kubernetes name
    display_name = Column(String(255), nullable=False)  # UI display name
    description = Column(Text, nullable=True)

    # Service type and categorization
    type = Column(String(50), nullable=False, index=True)
    namespace = Column(String(255), nullable=False, index=True)
    category = Column(
        String(100), nullable=True
    )  # e.g., 'infrastructure', 'development'

    # UI and access information
    icon = Column(String(255), nullable=True)  # Icon name or URL
    url = Column(String(500), nullable=True)  # Service URL (user-facing, may include SSO path)
    health_endpoint = Column(String(500), nullable=True)  # Health check endpoint (full URL)

    # State management
    is_enabled = Column(Boolean, default=True, nullable=False, index=True)
    original_replicas = Column(Integer, default=1, nullable=False)  # For re-enabling

    # Dependencies and metadata
    dependencies = Column(JSON, default=list, nullable=False)  # List of service names
    service_metadata = Column(
        JSON, default=dict, nullable=False
    )  # Additional service-specific data

    # Timestamps
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Scaling configuration
    resource_type = Column(String(50), nullable=True)  # deployment, statefulset, etc.
    resource_name = Column(String(255), nullable=True)  # Actual resource name in k8s
    min_replicas = Column(Integer, default=1, nullable=False)
    can_disable = Column(Boolean, default=True, nullable=False)

    # Relationships
    health_records = relationship(
        "ServiceHealth", back_populates="service", cascade="all, delete-orphan"
    )
    actions = relationship(
        "ServiceAction", back_populates="service", cascade="all, delete-orphan"
    )
    endpoints = relationship(
        "ServiceEndpoint", back_populates="service", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Service {self.name} ({self.type})>"

    @property
    def latest_health(self) -> Optional["ServiceHealth"]:
        """Get the most recent health check result"""
        if self.health_records:
            return max(self.health_records, key=lambda h: h.checked_at)
        return None

    @property
    def can_be_disabled(self) -> bool:
        """Check if this service can be disabled"""
        return self.type in ["optional", "user_app"]


class ServiceHealth(Base):
    """Model for tracking service health check results"""

    __tablename__ = "service_health"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy', 'unhealthy', 'unknown', 'disabled')",
            name="check_health_status",
        ),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign key to service
    service_id = Column(
        UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Health check results
    status = Column(String(50), nullable=False)
    response_time = Column(Integer, nullable=True)  # milliseconds
    status_code = Column(Integer, nullable=True)  # HTTP status code if applicable
    error_message = Column(Text, nullable=True)

    # Additional details
    details = Column(
        JSON, default=dict, nullable=False
    )  # Additional health check details

    # Timestamp
    checked_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    # Relationship
    service = relationship("Service", back_populates="health_records")

    def __repr__(self):
        return f"<ServiceHealth {self.service_id} {self.status} at {self.checked_at}>"


class ServiceAction(Base):
    """Model for tracking service state changes and actions"""

    __tablename__ = "service_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('enable', 'disable', 'restart', 'delete', 'update')",
            name="check_action_type",
        ),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign key to service
    service_id = Column(
        UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Action information
    action = Column(String(50), nullable=False)
    performed_by = Column(String(255), nullable=True)  # Username from auth

    # Additional details
    details = Column(JSON, default=dict, nullable=False)  # Action-specific details

    # Timestamp
    performed_at = Column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )

    # Relationship
    service = relationship("Service", back_populates="actions")

    def __repr__(self):
        return (
            f"<ServiceAction {self.action} on {self.service_id} at {self.performed_at}>"
        )


class ServiceEndpoint(Base):
    """Model for tracking multiple endpoints per service"""

    __tablename__ = "service_endpoints"
    __table_args__ = (
        CheckConstraint(
            "type IN ('http', 'grpc', 'tcp', 'postgres', 'redis', 'docker-registry', 'internal')",
            name="check_endpoint_type",
        ),
        CheckConstraint(
            "health_status IN ('healthy', 'unhealthy', 'unknown')",
            name="check_endpoint_health_status",
        ),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign key to service
    service_id = Column(
        UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Endpoint information
    name = Column(String(100), nullable=False)  # e.g., 'web', 'api', 'grpc'
    type = Column(String(50), nullable=False)  # http, grpc, tcp, etc.
    url = Column(String(500), nullable=True)
    port = Column(Integer, nullable=True)

    # Health check configuration
    health_url = Column(String(500), nullable=True)  # Full health check URL
    health_service = Column(String(200), nullable=True)  # For gRPC health checks

    # Metadata
    description = Column(Text, nullable=True)
    is_primary = Column(Boolean, default=False, nullable=False, index=True)
    is_internal = Column(
        Boolean, default=False, nullable=False
    )  # Internal-only endpoints

    # Health status
    last_health_check = Column(DateTime, nullable=True)
    health_status = Column(String(50), nullable=True)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationship
    service = relationship("Service", back_populates="endpoints")

    def __repr__(self):
        return f"<ServiceEndpoint {self.name} ({self.type}) for {self.service_id}>"
