"""Pydantic schemas for service management API"""

from datetime import datetime
from typing import List, Optional, Dict, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict, field_validator


# Enums
ServiceType = Literal["core", "optional", "user_app"]
ServiceStatus = Literal["healthy", "unhealthy", "unknown", "disabled"]
ServiceAction = Literal["enable", "disable", "restart", "delete", "update"]
EndpointType = Literal["http", "grpc", "tcp", "postgres", "redis", "docker-registry", "internal"]


# Base schemas
class ServiceBase(BaseModel):
    """Base schema for service data"""

    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    type: ServiceType
    namespace: str = Field(..., min_length=1, max_length=255)
    category: Optional[str] = Field(None, max_length=100)
    icon: Optional[str] = Field(None, max_length=255)
    url: Optional[str] = None  # Can be any URL type (http, https, postgresql, redis, etc.)
    health_endpoint: Optional[str] = Field(None, max_length=500)
    dependencies: List[str] = Field(default_factory=list)
    service_metadata: Dict[str, Any] = Field(default_factory=dict)


# Request schemas
class ServiceCreate(ServiceBase):
    """Schema for creating a new service"""

    pass


class ServiceUpdate(BaseModel):
    """Schema for updating a service"""

    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)
    icon: Optional[str] = Field(None, max_length=255)
    url: Optional[str] = None  # Can be any URL type (http, https, postgresql, redis, etc.)
    health_endpoint: Optional[str] = Field(None, max_length=500)
    dependencies: Optional[List[str]] = None
    service_metadata: Optional[Dict[str, Any]] = None


class ServiceToggle(BaseModel):
    """Schema for enabling/disabling a service"""

    is_enabled: bool
    reason: Optional[str] = None


class ServiceNameCheck(BaseModel):
    """Schema for checking service name availability"""

    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z][a-z0-9-]*$")
    type: ServiceType


# Response schemas
class ServiceHealth(BaseModel):
    """Schema for service health data"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_id: UUID
    status: ServiceStatus
    response_time: Optional[int] = None  # milliseconds
    status_code: Optional[int] = None
    error_message: Optional[str] = None
    checked_at: datetime
    details: Dict[str, Any] = Field(default_factory=dict)


class ServiceEndpointBase(BaseModel):
    """Base schema for service endpoint data"""

    name: str = Field(..., min_length=1, max_length=100)
    type: EndpointType
    url: Optional[str] = Field(None, max_length=500)
    port: Optional[int] = Field(None, ge=1, le=65535)
    health_url: Optional[str] = Field(None, max_length=500)
    health_service: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    is_primary: bool = False
    is_internal: bool = False


class ServiceEndpoint(ServiceEndpointBase):
    """Full endpoint schema for responses"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_id: UUID
    last_health_check: Optional[datetime] = None
    health_status: Optional[ServiceStatus] = None
    created_at: datetime
    updated_at: datetime


class ServiceActionResponse(BaseModel):
    """Schema for service action data"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_id: UUID
    action: ServiceAction
    performed_by: Optional[str] = None
    performed_at: datetime
    details: Dict[str, Any] = Field(default_factory=dict)


class Service(ServiceBase):
    """Full service schema for responses"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_enabled: bool
    original_replicas: int
    created_at: datetime
    updated_at: datetime

    # Scaling fields
    resource_type: Optional[str] = None
    resource_name: Optional[str] = None
    min_replicas: int = 1
    can_disable: bool = True

    # Computed fields
    latest_health: Optional[ServiceHealth] = None
    can_be_disabled: bool = False
    endpoints: List[ServiceEndpoint] = Field(default_factory=list)
    is_favorite: bool = False  # Whether the current user has favorited this service
    gpu_count: Optional[int] = None  # Number of GPUs used
    gpu_nodes: Optional[List[str]] = None  # Nodes where GPUs are allocated


class ServiceDetail(Service):
    """Detailed service schema with additional information"""

    health_history: List[ServiceHealth] = Field(default_factory=list)
    recent_actions: List[ServiceActionResponse] = Field(default_factory=list)
    resource_usage: Optional[Dict[str, Any]] = None
    pods_info: Optional[List[Dict[str, Any]]] = None


class ServiceList(BaseModel):
    """Schema for service list response"""

    services: List[Service]
    total: int
    filters: Optional[Dict[str, Any]] = None  # Optional filters info


class ServiceMinimal(BaseModel):
    """Minimal service schema for MCP list operations"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_name: str
    type: ServiceType
    is_enabled: bool
    category: Optional[str] = None


class ServiceListMinimal(BaseModel):
    """Minimal schema for MCP service list response"""

    services: List[ServiceMinimal]
    total: int


class ServiceNameCheckResponse(BaseModel):
    """Response for name availability check"""

    available: bool
    reason: Optional[str] = None
    existing_service: Optional[Dict[str, Any]] = None


class ServiceHealthHistory(BaseModel):
    """Schema for health history response"""

    current_status: ServiceStatus
    uptime_percentage: float
    monitoring_coverage: float  # Percentage of time we were actually monitoring
    health_history: List[ServiceHealth]
    total_checks: int
    failed_checks: int
    actual_checks: int  # Number of actual health checks (excluding gaps)
    theoretical_checks: Optional[int] = None  # Expected checks in time period
    check_interval_seconds: Optional[int] = None  # Check frequency


class ServiceDependencyInfo(BaseModel):
    """Schema for dependency information"""

    service_id: UUID
    name: str
    dependencies: List[Dict[str, Any]]  # List of dependent service info
    dependents: List[Dict[str, Any]]  # List of services that depend on this
    can_disable: bool
    disable_warning: Optional[str] = None


# Validation helpers
class ServiceValidation:
    """Helper class for service validation"""

    # Reserved names for core services
    CORE_SERVICE_NAMES = {
        "keycloak",
        "harbor",
        "gitea",
        "argocd",
        "argo-workflows",
        "postgresql",
        "minio",
        "devpi",
        "thinkube-control",
    }

    # Reserved names for optional services
    OPTIONAL_SERVICE_NAMES = {
        "prometheus",
        "grafana",
        "opensearch",
        "jupyterhub",
        "code-server",
        "pgadmin",
        "qdrant",
        "knative",
    }

    @classmethod
    def is_reserved_name(cls, name: str) -> bool:
        """Check if a name is reserved"""
        return name in cls.CORE_SERVICE_NAMES or name in cls.OPTIONAL_SERVICE_NAMES

    @classmethod
    def get_reserved_type(cls, name: str) -> Optional[str]:
        """Get the type of reserved service"""
        if name in cls.CORE_SERVICE_NAMES:
            return "core"
        elif name in cls.OPTIONAL_SERVICE_NAMES:
            return "optional"
        return None


# WebSocket schemas for real-time updates
class ServiceStatusUpdate(BaseModel):
    """Schema for real-time service status updates"""

    service_id: UUID
    status: ServiceStatus
    response_time: Optional[int] = None
    timestamp: datetime


class ServiceStateChange(BaseModel):
    """Schema for service state change notifications"""

    service_id: UUID
    action: ServiceAction
    is_enabled: bool
    performed_by: str
    timestamp: datetime


# 🤖 Generated with [Claude Code](https://claude.ai/code)
