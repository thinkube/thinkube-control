# app/api/cicd_postgres.py
"""CI/CD monitoring endpoints using PostgreSQL for persistence."""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from uuid import UUID
import json
import asyncio
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    Depends,
)
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, or_, text
import logging

from app.core.config import settings
from app.core.security import get_current_user
from app.core.api_tokens import get_current_user_dual_auth
from app.db.cicd_session import get_cicd_db
from app.models.cicd import (
    Pipeline,
    PipelineStage,
    PipelineMetric,
    PipelineStatus,
    StageStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Display formatting helpers
def generate_mermaid_gantt(pipeline: Pipeline) -> str:
    """Generate Mermaid Gantt chart syntax for a pipeline."""
    if not pipeline.stages:
        return "gantt\n    title No stages\n    dateFormat X\n    axisFormat %s"

    # Sort stages by start time
    sorted_stages = sorted(
        pipeline.stages,
        key=lambda s: s.started_at if s.started_at else datetime.utcnow(),
    )

    # Build the Gantt chart
    gantt = "gantt\n"
    # Escape title to avoid syntax errors
    safe_title = (
        pipeline.app_name.replace(":", "-").replace(",", "").replace('"', "'").strip()
    )
    gantt += f"    title {safe_title} Pipeline Execution\n"
    gantt += "    dateFormat X\n"  # Unix timestamp format
    gantt += "    axisFormat %H:%M:%S\n"

    # Each task becomes its own section (appears on left)
    for index, stage in enumerate(sorted_stages):
        start_time = int(stage.started_at.timestamp()) if stage.started_at else 0
        end_time = (
            int(stage.completed_at.timestamp())
            if stage.completed_at
            else int(datetime.utcnow().timestamp())
        )
        duration = end_time - start_time

        # Create section name from stage name
        section_name = stage.stage_name.replace("_", " ").title()
        # Escape special characters
        section_name = (
            section_name.replace(":", "")
            .replace(",", "")
            .replace("[", "")
            .replace("]", "")
            .replace("{", "")
            .replace("}", "")
            .replace('"', "'")
            .replace("\n", " ")
            .strip()
        )

        # Add line break for long section names to prevent overlap
        if len(section_name) > 15:
            # Find a good break point (space near middle)
            mid = len(section_name) // 2
            space_pos = section_name.find(" ", mid - 5)
            if space_pos > 0:
                section_name = (
                    section_name[:space_pos] + "<br/>" + section_name[space_pos + 1 :]
                )

        # Add section for this task
        gantt += f"section {section_name}\n"

        # Determine status for styling based on stage type and status
        # Use creative mapping: done=green (deployment), active=blue (workflow), regular=orange (other)
        status = ""
        stage_lower = stage.stage_name.lower()

        # Check if it's a deployment task (ArgoCD)
        if "deploy" in stage_lower or "argocd" in stage_lower or "sync" in stage_lower:
            if stage.status == StageStatus.SUCCEEDED:
                status = "done"  # Will be styled green for deployments
            elif stage.status == StageStatus.FAILED:
                status = "crit"
            elif stage.status == StageStatus.RUNNING:
                status = "active"
        # Check if it's a workflow task
        elif (
            "workflow" in stage_lower or "build" in stage_lower or "test" in stage_lower
        ):
            if stage.status == StageStatus.FAILED:
                status = "crit"
            else:
                status = "active"  # Will be styled blue for workflows
        # Everything else gets no special status (will be styled orange)
        else:
            if stage.status == StageStatus.FAILED:
                status = "crit"
            # No status for other successful/running tasks

        # Determine task type for CSS class naming
        task_type = "other"
        if "deploy" in stage_lower or "argocd" in stage_lower or "sync" in stage_lower:
            task_type = "deployment"
        elif (
            "workflow" in stage_lower or "build" in stage_lower or "test" in stage_lower
        ):
            task_type = "workflow"

        task_id = f"{task_type}{index}"  # Include type in task ID for CSS targeting

        # For 0-duration tasks, set end time to start time + 1 to make them visible
        if duration == 0:
            end_time = start_time + 1

        # Task with space as label (Mermaid requires some text)
        if status:
            gantt += f"     . :{status}, {task_id}, {start_time}, {end_time}\n"
        else:
            gantt += f"     . :{task_id}, {start_time}, {end_time}\n"

    return gantt


def format_duration(seconds: Optional[float]) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds is None:
        return "Unknown"

    if seconds < 0:
        logger.warning(f"Negative duration detected: {seconds}s")
        return "0s"

    if seconds < 1:
        return "0s"
    elif seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    else:
        hours = int(seconds / 3600)
        mins = int((seconds % 3600) / 60)
        return f"{hours}h {mins}m"


def calculate_stage_display(stage: PipelineStage) -> Dict[str, Any]:
    """Calculate display fields for a stage."""
    display = {
        "name": stage.stage_name.replace("_", " ").title(),
        "status": stage.status.value.capitalize(),
        "isRunning": stage.status == StageStatus.RUNNING,
        "hasError": stage.status == StageStatus.FAILED,
    }

    # Start time display
    if stage.started_at:
        display["startTime"] = stage.started_at.strftime("%I:%M:%S %p")
    else:
        display["startTime"] = "Not started"

    # Duration calculation
    if not stage.started_at:
        display["duration"] = "Not started"
    elif not stage.completed_at:
        if stage.status == StageStatus.RUNNING:
            # Calculate running duration
            duration = (datetime.utcnow() - stage.started_at).total_seconds()
            display["duration"] = format_duration(duration) + " (running)"
        else:
            display["duration"] = "Pending"
    else:
        # Calculate completed duration
        duration = (stage.completed_at - stage.started_at).total_seconds()
        display["duration"] = format_duration(duration)

    return display


# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, pipeline_id: str):
        await websocket.accept()
        if pipeline_id not in self.active_connections:
            self.active_connections[pipeline_id] = []
        self.active_connections[pipeline_id].append(websocket)

    def disconnect(self, websocket: WebSocket, pipeline_id: str):
        if pipeline_id in self.active_connections:
            self.active_connections[pipeline_id].remove(websocket)
            if not self.active_connections[pipeline_id]:
                del self.active_connections[pipeline_id]

    async def send_pipeline_update(self, pipeline_id: str, update: dict):
        if pipeline_id in self.active_connections:
            for connection in self.active_connections[pipeline_id]:
                try:
                    await connection.send_json(update)
                except:
                    # Connection might be closed
                    pass


manager = ConnectionManager()


@router.get("/health")
async def health_check(db: Session = Depends(get_cicd_db)) -> Dict[str, str]:
    """Health check endpoint for CI/CD monitoring API."""
    try:
        # Test database connection
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {"status": "healthy", "service": "cicd-monitoring", "database": db_status}


@router.get("/pipelines")
async def list_pipelines(
    app_name: Optional[str] = Query(None, description="Filter by application name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    workflow_uid: Optional[str] = Query(None, description="Filter by workflow UID"),
    limit: int = Query(20, description="Maximum number of pipelines to return"),
    offset: int = Query(0, description="Number of pipelines to skip"),
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """List recent CI/CD pipelines with optional filtering."""
    query = db.query(Pipeline)

    # Apply filters
    if app_name:
        query = query.filter(Pipeline.app_name == app_name)
    if status:
        query = query.filter(Pipeline.status == status)
    if workflow_uid:
        query = query.filter(Pipeline.workflow_uid == workflow_uid)

    # Get total count
    total = query.count()

    # Get paginated results
    pipelines = (
        query.order_by(desc(Pipeline.started_at)).offset(offset).limit(limit).all()
    )

    # Convert to dict format
    result = []
    for pipeline in pipelines:
        # Calculate duration in seconds
        duration = None
        if pipeline.started_at and pipeline.completed_at:
            duration = (pipeline.completed_at - pipeline.started_at).total_seconds()
        elif pipeline.started_at and pipeline.status == PipelineStatus.RUNNING:
            duration = (datetime.utcnow() - pipeline.started_at).total_seconds()

        pipeline_dict = {
            "id": str(pipeline.id),
            "appName": pipeline.app_name,
            "branch": pipeline.branch,
            "commitSha": pipeline.commit_sha,
            "commitMessage": pipeline.commit_message,
            "authorEmail": pipeline.author_email,
            "startedAt": (
                pipeline.started_at.timestamp() if pipeline.started_at else None
            ),
            "completedAt": (
                pipeline.completed_at.timestamp() if pipeline.completed_at else None
            ),
            "status": pipeline.status.value if pipeline.status else "unknown",
            "triggerType": pipeline.trigger_type,
            "stageCount": len(pipeline.stages),
            "duration": (
                duration * 1000 if duration is not None else None
            ),  # Convert to milliseconds
        }
        result.append(pipeline_dict)

    return {"pipelines": result, "total": total, "limit": limit, "offset": offset}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(
    pipeline_id: UUID,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Get complete pipeline details with stages and events."""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Build response with stages including display fields
    stages = []
    for stage in pipeline.stages:
        stage_dict = {
            "id": str(stage.id),
            "stageName": stage.stage_name,
            "component": stage.component,
            "status": stage.status.value,
            "startedAt": stage.started_at.timestamp() if stage.started_at else None,
            "completedAt": (
                stage.completed_at.timestamp() if stage.completed_at else None
            ),
            "errorMessage": stage.error_message,
            "details": stage.details,
            "duration": (
                (stage.completed_at - stage.started_at).total_seconds()
                if stage.completed_at and stage.started_at
                else None
            ),
            "display": calculate_stage_display(stage),  # Add display fields
        }
        stages.append(stage_dict)

    # Events removed - using stages only

    # Calculate pipeline display fields
    pipeline_display = {
        "status": pipeline.status.value.capitalize() if pipeline.status else "Unknown",
        "startTime": (
            pipeline.started_at.strftime("%I:%M:%S %p")
            if pipeline.started_at
            else "Unknown"
        ),
    }

    # Calculate total pipeline duration
    if pipeline.started_at and pipeline.completed_at:
        total_duration = (pipeline.completed_at - pipeline.started_at).total_seconds()
        pipeline_display["duration"] = format_duration(total_duration)
    elif pipeline.started_at and pipeline.status == PipelineStatus.RUNNING:
        running_duration = (datetime.utcnow() - pipeline.started_at).total_seconds()
        pipeline_display["duration"] = format_duration(running_duration) + " (running)"
    else:
        pipeline_display["duration"] = "Unknown"

    return {
        "id": str(pipeline.id),
        "appName": pipeline.app_name,
        "branch": pipeline.branch,
        "commitSha": pipeline.commit_sha,
        "commitMessage": pipeline.commit_message,
        "authorEmail": pipeline.author_email,
        "startedAt": pipeline.started_at.timestamp() if pipeline.started_at else None,
        "completedAt": (
            pipeline.completed_at.timestamp() if pipeline.completed_at else None
        ),
        "status": pipeline.status.value if pipeline.status else "unknown",
        "triggerType": pipeline.trigger_type,
        "stages": stages,
        "display": pipeline_display,  # Add display fields
        "mermaidGantt": generate_mermaid_gantt(pipeline),  # Add Mermaid diagram
    }


# Event endpoints removed - using stage-based approach only

# Generic endpoints removed - use source-specific endpoints in cicd_sources.py instead


@router.patch("/pipelines/{pipeline_id}")
async def update_pipeline(
    pipeline_id: UUID,
    pipeline_update: Dict[str, Any],
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, str]:
    """Update pipeline status and completion time."""
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    # Update status
    if "status" in pipeline_update:
        pipeline.status = PipelineStatus(pipeline_update["status"])

    # Update completed time
    if "completedAt" in pipeline_update:
        pipeline.completed_at = datetime.fromtimestamp(pipeline_update["completedAt"])

    db.commit()

    # Send WebSocket notification for pipeline update
    await manager.send_pipeline_update(
        str(pipeline_id),
        {
            "pipelineId": str(pipeline_id),
            "status": pipeline.status.value if pipeline.status else None,
            "completedAt": (
                pipeline.completed_at.timestamp() if pipeline.completed_at else None
            ),
        },
    )

    return {"status": "updated"}


# Event creation endpoint removed - using stage-based approach only


@router.websocket("/ws/pipelines/{pipeline_id}")
async def pipeline_stream(websocket: WebSocket, pipeline_id: str):
    """WebSocket endpoint for real-time pipeline updates."""
    await manager.connect(websocket, pipeline_id)
    try:
        while True:
            # Keep connection alive
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        manager.disconnect(websocket, pipeline_id)


# Global event stream removed - using pipeline-specific WebSocket only


@router.get("/metrics")
async def get_metrics(
    app_name: str = Query(..., description="Application name"),
    period: str = Query("7d", description="Time period (e.g., 7d, 24h, 30d)"),
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, Any]:
    """Get pipeline metrics and statistics for an application."""
    # Parse period
    period_map = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    delta = period_map.get(period, timedelta(days=7))
    cutoff_time = datetime.utcnow() - delta

    # Query pipelines within time period
    pipelines = (
        db.query(Pipeline)
        .filter(and_(Pipeline.app_name == app_name, Pipeline.started_at >= cutoff_time))
        .all()
    )

    # Calculate metrics
    total = len(pipelines)
    successful = len([p for p in pipelines if p.status == PipelineStatus.SUCCEEDED])
    failed = len([p for p in pipelines if p.status == PipelineStatus.FAILED])

    # Calculate average duration
    durations = []
    for pipeline in pipelines:
        if pipeline.completed_at and pipeline.started_at:
            duration = (pipeline.completed_at - pipeline.started_at).total_seconds()
            durations.append(duration)

    avg_duration = sum(durations) / len(durations) if durations else 0

    # Get metrics from database
    metrics = (
        db.query(PipelineMetric)
        .join(Pipeline)
        .filter(and_(Pipeline.app_name == app_name, Pipeline.started_at >= cutoff_time))
        .all()
    )

    # Group metrics by name
    metric_summary = {}
    for metric in metrics:
        if metric.metric_name not in metric_summary:
            metric_summary[metric.metric_name] = []
        metric_summary[metric.metric_name].append(float(metric.metric_value))

    # Calculate averages for each metric
    metric_averages = {
        name: sum(values) / len(values) for name, values in metric_summary.items()
    }

    return {
        "appName": app_name,
        "period": period,
        "metrics": {
            "totalPipelines": total,
            "successfulPipelines": successful,
            "failedPipelines": failed,
            "successRate": (successful / total * 100) if total > 0 else 0,
            "averageDuration": avg_duration,
            "pipelinesPerDay": total / delta.days if delta.days > 0 else total,
            "customMetrics": metric_averages,
        },
    }


@router.post("/repositories")
async def register_repository(
    request: dict,
    current_user=Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
):
    """Register a repository for CI/CD monitoring"""
    try:
        # Extract repository information
        repository_url = request.get("repository_url")
        repository_name = request.get("repository_name")
        active = request.get("active", True)

        # Store repository configuration (for now, just log it)
        logger.info(
            f"Registering repository {repository_name} at {repository_url} for monitoring"
        )

        # In a full implementation, this would:
        # 1. Store repository config in database
        # 2. Configure webhook interceptors
        # 3. Set up event collectors

        return {
            "status": "registered",
            "repository": repository_name,
            "url": repository_url,
            "active": active,
            "message": "Repository registered for CI/CD monitoring",
        }
    except Exception as e:
        logger.error(f"Failed to register repository: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/applications")
async def list_applications(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> List[str]:
    """List all applications that have CI/CD pipelines."""
    # Get distinct app names
    apps = db.query(Pipeline.app_name).distinct().all()
    return sorted([app[0] for app in apps])


@router.post("/pipelines/{pipeline_id}/metrics")
async def add_metric(
    pipeline_id: UUID,
    metric_data: Dict[str, Any],
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_cicd_db),
) -> Dict[str, str]:
    """Add a metric to a pipeline."""
    # Verify pipeline exists
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    metric = PipelineMetric(
        pipeline_id=pipeline_id,
        metric_name=metric_data["name"],
        metric_value=metric_data["value"],
        unit=metric_data.get("unit"),
    )

    db.add(metric)
    db.commit()

    return {"status": "created"}


# 🤖 Generated with Claude
