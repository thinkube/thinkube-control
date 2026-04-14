"""
Pydantic schemas for template deployments
"""

from pydantic import BaseModel, HttpUrl
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID


class TemplateDeployAsyncRequest(BaseModel):
    """Request model for async template deployment"""

    template_url: HttpUrl
    template_name: str
    variables: Dict[str, Any] = {}
    execution_mode: Optional[str] = "background"  # "websocket" or "background"

    class Config:
        json_schema_extra = {
            "example": {
                "template_url": "https://github.com/thinkube/tkt-webapp-vue-fastapi",
                "template_name": "my-awesome-app",
                "variables": {
                    "project_description": "My awesome application",
                    "author_name": "John Doe",
                    "author_email": "john@example.com",
                },
                "execution_mode": "websocket",
            }
        }


class DeploymentResponse(BaseModel):
    """Response model for deployment creation"""

    deployment_id: str
    status: str
    message: str
    websocket_url: Optional[str] = None
    requires_confirmation: Optional[bool] = False
    conflict_warning: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "deployment_id": "123e4567-e89b-12d3-a456-426614174000",
                "status": "pending",
                "message": "Deployment queued successfully",
                "websocket_url": "/ws/template/deploy/123e4567-e89b-12d3-a456-426614174000",
            }
        }


class DeploymentStatus(BaseModel):
    """Deployment status response"""

    id: str
    name: str
    template_url: str
    status: str
    variables: Optional[Dict[str, Any]] = None
    output: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by: str
    duration: Optional[float] = None

    class Config:
        from_attributes = True


class DeploymentLogEntry(BaseModel):
    """Single deployment log entry"""

    id: str
    deployment_id: str
    timestamp: datetime
    type: str
    message: str
    task_name: Optional[str] = None
    task_number: Optional[int] = None

    class Config:
        from_attributes = True


class DeploymentLogsResponse(BaseModel):
    """Response model for deployment logs"""

    deployment_id: str
    logs: List[DeploymentLogEntry]
    total_count: int
    has_more: bool

    class Config:
        json_schema_extra = {
            "example": {
                "deployment_id": "123e4567-e89b-12d3-a456-426614174000",
                "logs": [
                    {
                        "id": "456e7890-e89b-12d3-a456-426614174000",
                        "deployment_id": "123e4567-e89b-12d3-a456-426614174000",
                        "timestamp": "2024-01-15T10:30:00Z",
                        "type": "task",
                        "message": "TASK [Create namespace]",
                        "task_name": "Create namespace",
                        "task_number": 1,
                    }
                ],
                "total_count": 150,
                "has_more": True,
            }
        }


class DeploymentListResponse(BaseModel):
    """Response model for listing deployments"""

    deployments: List[DeploymentStatus]
    total_count: int
    page: int
    page_size: int

    class Config:
        json_schema_extra = {
            "example": {
                "deployments": [],
                "total_count": 10,
                "page": 1,
                "page_size": 20,
            }
        }


# ==================== Stack Deployment Schemas ====================


class StackTemplateEntry(BaseModel):
    """A template entry within a stack manifest"""

    name: str
    repo: str
    params: Optional[Dict[str, Any]] = None
    env: Optional[Dict[str, str]] = None


class StackDeployRequest(BaseModel):
    """Request to deploy a ThinkubeStack"""

    stack_yaml: str  # Raw YAML content of the stack manifest

    class Config:
        json_schema_extra = {
            "example": {
                "stack_yaml": "apiVersion: thinkube.io/v1\nkind: ThinkubeStack\nmetadata:\n  name: my-stack\ntemplates:\n  - name: embeddings\n    repo: thinkube/tkt-text-embeddings\n"
            }
        }


class StackTemplateStatus(BaseModel):
    """Status of a single template within a stack deployment"""

    name: str
    repo: str
    status: str  # pending, deploying, healthy, failed, skipped
    deployment_id: Optional[str] = None
    error: Optional[str] = None


class StackDeployResponse(BaseModel):
    """Response for stack deployment"""

    stack_id: str
    stack_name: str
    status: str  # pending, deploying, partially_deployed, healthy, failed
    message: str
    templates: List[StackTemplateStatus]
    deploy_order: List[str]  # Topological order of template names
