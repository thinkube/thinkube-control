# app/api/cicd_sources.py
"""Source-specific CI/CD endpoints for different integration points."""

from typing import Dict, Any, Optional
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
import logging

from app.core.api_tokens import get_current_user_dual_auth
from app.db.cicd_session import get_cicd_db
from app.models.cicd import Pipeline, PipelineStage, StageStatus, PipelineStatus

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic models for type safety
class ArgoWorkflowPipeline(BaseModel):
    """Pipeline creation from Argo Workflow (shell script)"""

    appName: str
    branch: str
    commitSha: str
    commitMessage: Optional[str]
    authorEmail: Optional[str]
    webhookTimestamp: str  # ISO8601 timestamp from Argo Events
    triggerType: str = "webhook"
    workflowUid: str  # Argo Workflow UID for reliable tracking


class ArgoWorkflowStage(BaseModel):
    """Stage creation from Argo Workflow (matches pipeline timestamp format)"""

    stageName: str
    component: str
    status: str = "SUCCEEDED"  # webhook_received is always completed
    startedAt: str  # ISO8601 timestamp like "2025-06-25T21:01:18Z"
    completedAt: Optional[str] = None  # ISO8601 timestamp
    details: Dict[str, Any] = {}


class GiteaWebhookStage(BaseModel):
    """Stage creation from initial webhook"""

    stageName: str
    component: str
    status: str = "SUCCEEDED"
    startedAt: float  # Unix timestamp
    completedAt: float  # Unix timestamp
    details: Dict[str, Any] = {}


class ShellScriptStage(BaseModel):
    """Stage creation from shell scripts"""

    stageName: str
    component: str
    scriptVersion: str
    details: Dict[str, Any] = {}


class ShellStageUpdate(BaseModel):
    """Stage update from shell scripts"""

    status: str  # SUCCEEDED or FAILED
    errorMessage: Optional[str] = None
    details: Optional[Dict[str, Any]] = {}
    completionTimestamp: Optional[str] = None  # ISO 8601 timestamp


class HarborWebhookStage(BaseModel):
    """Stage creation from Harbor webhook adapter"""

    stageName: str = "image_push"
    component: str = "harbor"
    appName: str
    tag: str
    backend: str
    frontend: str
    adapterVersion: str = "0.1.1"


class GitOpsUpdateStage(BaseModel):
    """GitOps update stage from webhook adapter"""

    stageName: str = "gitops_update"
    component: str = "webhook-adapter"
    appName: str
    tag: str
    status: str = "RUNNING"
    adapterVersion: str = "0.1.1"


class ArgoCDPipeline(BaseModel):
    """Pipeline creation from ArgoCD deployment"""

    appName: str
    branch: str = "main"
    commitSha: str = "argocd-sync"
    commitMessage: str = "ArgoCD sync deployment"
    triggerType: str = "argocd"
    deploymentTimestamp: float  # Unix timestamp


class ArgoCDDeploymentStage(BaseModel):
    """Deployment stage from ArgoCD PostSync hook"""

    stageName: str = "deployment_completed"
    component: str = "argocd"
    appName: str
    namespace: str
    adapterVersion: str = "0.1.0"


# Pipeline creation endpoints
@router.post("/pipelines/argo-workflow")
async def create_pipeline_from_argo(
    data: ArgoWorkflowPipeline,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create pipeline from Argo Workflow (expects ISO8601 timestamp)."""
    logger.info(f"Creating pipeline from Argo Workflow: {data.appName}")

    # Parse ISO8601 timestamp
    webhook_time = datetime.fromisoformat(data.webhookTimestamp.replace("Z", "+00:00"))

    pipeline = Pipeline(
        app_name=data.appName,
        branch=data.branch,
        commit_sha=data.commitSha,
        commit_message=data.commitMessage,
        author_email=data.authorEmail,
        trigger_type=data.triggerType,
        workflow_uid=data.workflowUid,
        started_at=webhook_time,
    )

    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)

    return {
        "id": str(pipeline.id),
        "appName": pipeline.app_name,
        "status": "created",
        "timestamp": webhook_time.timestamp(),
    }


@router.post("/pipelines/argocd")
async def create_pipeline_from_argocd(
    data: ArgoCDPipeline,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create pipeline from ArgoCD deployment (expects Unix timestamp)."""
    logger.info(f"Creating pipeline from ArgoCD deployment: {data.appName}")

    # Convert Unix timestamp to datetime
    deployment_time = datetime.fromtimestamp(data.deploymentTimestamp)

    pipeline = Pipeline(
        app_name=data.appName,
        branch=data.branch,
        commit_sha=data.commitSha,
        commit_message=data.commitMessage,
        trigger_type=data.triggerType,
        started_at=deployment_time,
    )

    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)

    return {
        "id": str(pipeline.id),
        "appName": pipeline.app_name,
        "status": "created",
        "timestamp": deployment_time.timestamp(),
    }


# Stage creation endpoints
@router.post("/pipelines/{pipeline_id}/stages/gitea-webhook")
async def create_stage_from_gitea(
    pipeline_id: UUID,
    data: GiteaWebhookStage,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create stage from Gitea webhook data (pre-completed stages)."""
    logger.info(f"Creating Gitea webhook stage: {data.stageName}")

    # Verify pipeline exists
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Convert timestamps
    started_at = datetime.fromtimestamp(data.startedAt)
    completed_at = datetime.fromtimestamp(data.completedAt)

    # Add source info to details
    details = data.details.copy()
    details["source"] = "gitea-webhook"
    details["scriptVersion"] = "0.1.1"

    stage = PipelineStage(
        pipeline_id=pipeline_id,
        stage_name=data.stageName,
        component=data.component,
        status=StageStatus(data.status),
        started_at=started_at,
        completed_at=completed_at,
        details=details,
    )

    db.add(stage)
    db.commit()
    db.refresh(stage)

    return {
        "id": str(stage.id),
        "status": "created",
        "duration": (completed_at - started_at).total_seconds(),
    }


@router.post("/pipelines/{pipeline_id}/stages/argo-workflow")
async def create_stage_from_argo_workflow(
    pipeline_id: UUID,
    data: ArgoWorkflowStage,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create stage from Argo Workflow (accepts ISO8601 timestamps)."""
    logger.info(f"Creating Argo workflow stage: {data.stageName}")

    # Verify pipeline exists
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Parse ISO8601 timestamps (same as pipeline creation)
    started_at = datetime.fromisoformat(data.startedAt.replace("Z", "+00:00"))
    completed_at = None
    if data.completedAt:
        completed_at = datetime.fromisoformat(data.completedAt.replace("Z", "+00:00"))

    # Add source info
    details = data.details.copy()
    details["source"] = "argo-workflow"

    stage = PipelineStage(
        pipeline_id=pipeline_id,
        stage_name=data.stageName,
        component=data.component,
        status=StageStatus(data.status),
        started_at=started_at,
        completed_at=completed_at,
        details=details,
    )

    db.add(stage)
    db.commit()
    db.refresh(stage)

    return {"id": str(stage.id), "status": "created"}


@router.post("/pipelines/{pipeline_id}/stages/shell")
async def create_stage_from_shell(
    pipeline_id: UUID,
    data: ShellScriptStage,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create stage from shell script (wrap-stage.sh)."""
    logger.info(f"Creating shell stage: {data.stageName}")

    # Verify pipeline exists
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Add metadata to details
    details = data.details.copy()
    details["source"] = "shell-script"
    details["scriptVersion"] = data.scriptVersion

    stage = PipelineStage(
        pipeline_id=pipeline_id,
        stage_name=data.stageName,
        component=data.component,
        status=StageStatus.PENDING,
        started_at=datetime.utcnow(),  # Backend sets time
        details=details,
    )

    db.add(stage)
    db.commit()
    db.refresh(stage)

    # Immediately transition to RUNNING
    stage.status = StageStatus.RUNNING
    db.commit()

    return {"id": str(stage.id), "status": "created"}


@router.post("/pipelines/{pipeline_id}/stages/harbor")
async def create_stage_from_harbor(
    pipeline_id: UUID,
    data: HarborWebhookStage,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create image_push stage from Harbor webhook adapter."""
    logger.info(f"Creating Harbor image_push stage for {data.appName}:{data.tag}")

    # Verify pipeline exists
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Check for existing image_push stage with same tag
    # Note: Can't use JSON operators in filter, need to check in Python
    existing_stages = (
        db.query(PipelineStage)
        .filter(
            PipelineStage.pipeline_id == pipeline_id,
            PipelineStage.stage_name == "image_push",
        )
        .all()
    )

    existing = None
    for stage in existing_stages:
        if stage.details.get("tag") == data.tag:
            existing = stage
            break

    if existing:
        logger.info(f"image_push stage already exists for tag {data.tag}")
        return {"id": str(existing.id), "status": "already_exists"}

    # Harbor webhook means images are ready - instant completion
    now = datetime.utcnow()

    stage = PipelineStage(
        pipeline_id=pipeline_id,
        stage_name=data.stageName,
        component=data.component,
        status=StageStatus.SUCCEEDED,
        started_at=now,
        completed_at=now,  # Instant
        details={
            "source": "harbor-webhook",
            "adapterVersion": data.adapterVersion,
            "appName": data.appName,
            "tag": data.tag,
            "backend": data.backend,
            "frontend": data.frontend,
        },
    )

    db.add(stage)
    db.commit()
    db.refresh(stage)

    return {"id": str(stage.id), "status": "created", "duration": 0.0}


@router.post("/pipelines/{pipeline_id}/stages/gitops")
async def create_gitops_stage(
    pipeline_id: UUID,
    data: GitOpsUpdateStage,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create gitops_update stage from webhook adapter."""
    logger.info(f"Creating GitOps update stage for {data.appName}:{data.tag}")

    # Verify pipeline exists
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    stage = PipelineStage(
        pipeline_id=pipeline_id,
        stage_name=data.stageName,
        component=data.component,
        status=StageStatus(data.status),
        started_at=datetime.utcnow(),
        details={
            "source": "webhook-adapter",
            "adapterVersion": data.adapterVersion,
            "appName": data.appName,
            "tag": data.tag,
        },
    )

    db.add(stage)
    db.commit()
    db.refresh(stage)

    return {"id": str(stage.id), "status": "created"}


@router.post("/pipelines/{pipeline_id}/stages/argocd")
async def create_argocd_deployment_stage(
    pipeline_id: UUID,
    data: ArgoCDDeploymentStage,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Create deployment_completed stage from ArgoCD PostSync hook."""
    logger.info(f"Creating ArgoCD deployment stage for {data.appName}")

    # Verify pipeline exists
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # ArgoCD deployment is instant - already completed when hook runs
    now = datetime.utcnow()

    stage = PipelineStage(
        pipeline_id=pipeline_id,
        stage_name=data.stageName,
        component=data.component,
        status=StageStatus.SUCCEEDED,
        started_at=now,
        completed_at=now,  # Instant
        details={
            "source": "argocd-postsync",
            "adapterVersion": data.adapterVersion,
            "appName": data.appName,
            "namespace": data.namespace,
        },
    )

    db.add(stage)
    db.commit()
    db.refresh(stage)

    # Mark pipeline as succeeded when deployment completes
    pipeline.status = PipelineStatus.SUCCEEDED
    pipeline.completed_at = now
    db.commit()

    return {"id": str(stage.id), "status": "created", "duration": 0.0}


# Stage update endpoints
@router.put("/pipelines/{pipeline_id}/stages/{stage_id}/shell-complete")
async def complete_stage_from_shell(
    pipeline_id: UUID,
    stage_id: UUID,
    data: ShellStageUpdate,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Complete a stage from shell script."""
    logger.info(f"Completing shell stage {stage_id} with status {data.status}")

    stage = (
        db.query(PipelineStage)
        .filter(PipelineStage.id == stage_id, PipelineStage.pipeline_id == pipeline_id)
        .first()
    )

    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")

    # Update stage
    stage.status = StageStatus(data.status)
    stage.error_message = data.errorMessage

    # Only set completed_at when stage is actually complete
    if data.status in ["SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED"]:
        if not data.completionTimestamp:
            raise HTTPException(
                status_code=400,
                detail="completionTimestamp is required for final stage states",
            )
        # Use the actual completion time from the script
        stage.completed_at = datetime.fromisoformat(
            data.completionTimestamp.replace("Z", "+00:00")
        )

    # Add completion details
    if data.details:
        stage.details.update(data.details)

    db.commit()

    # Calculate duration only if stage is complete
    duration = None
    if stage.completed_at:
        duration = (stage.completed_at - stage.started_at).total_seconds()

    response = {"status": "updated", "finalStatus": stage.status.value}
    if duration is not None:
        response["duration"] = duration

    return response


@router.put("/pipelines/{pipeline_id}/stages/{stage_id}/gitops-complete")
async def complete_gitops_stage(
    pipeline_id: UUID,
    stage_id: UUID,
    status: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Complete a GitOps stage from webhook adapter."""
    logger.info(f"Completing GitOps stage {stage_id} with status {status}")

    stage = (
        db.query(PipelineStage)
        .filter(PipelineStage.id == stage_id, PipelineStage.pipeline_id == pipeline_id)
        .first()
    )

    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")

    # Update stage
    stage.status = StageStatus(status)
    stage.completed_at = datetime.utcnow()

    db.commit()

    duration = (stage.completed_at - stage.started_at).total_seconds()

    return {"status": "updated", "duration": duration}


# 🤖 Generated with Claude
