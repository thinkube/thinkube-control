# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
The run queue: the run in progress, the runs waiting, and the latest results.

Deployment runs (optional components, template deploys, the code-server
redeploy) start one at a time from the queue; see app/services/run_queue.py.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.deployments import TemplateDeployment
from app.services.run_queue import FINISHED, QUEUED, cancel_queued

router = APIRouter(tags=["runs"])

RECENT_LIMIT = 10


class RunEntry(BaseModel):
    """One run in the queue view."""
    id: str
    name: str
    kind: str
    status: str
    queue_position: Optional[int] = None
    output: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class RunQueueResponse(BaseModel):
    """The run in progress, the queued runs in start order, and the latest finished runs."""
    active: Optional[RunEntry] = None
    queued: List[RunEntry]
    recent: List[RunEntry]


def _kind(run: TemplateDeployment) -> str:
    url = run.template_url or ""
    if url.startswith("core://code-server/redeploy"):
        return "code-server redeploy"
    if url.startswith("optional://"):
        return "optional component uninstall" if url.endswith("/uninstall") else "optional component install"
    if url.startswith(("image-mirror:", "harbor-remirror:")):
        return "image mirror"
    return "template deploy"


def _entry(run: TemplateDeployment, queue_position: Optional[int] = None) -> RunEntry:
    return RunEntry(
        id=str(run.id),
        name=run.name,
        kind=_kind(run),
        status=run.status,
        queue_position=queue_position,
        output=run.output,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.get("", response_model=RunQueueResponse, operation_id="list_runs")
async def list_runs(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """The run in progress, the queued runs with their positions, and the latest finished runs.

    Deployment runs start one at a time. get_deployment_status and
    get_deployment_logs take any of these ids.
    """
    active = (
        db.query(TemplateDeployment)
        .filter(TemplateDeployment.status.in_(["pending", "running"]))
        .order_by(TemplateDeployment.created_at.asc())
        .first()
    )
    queued = (
        db.query(TemplateDeployment)
        .filter(TemplateDeployment.status == QUEUED)
        .order_by(TemplateDeployment.created_at.asc())
        .all()
    )
    recent = (
        db.query(TemplateDeployment)
        .filter(TemplateDeployment.status.in_(FINISHED))
        .order_by(TemplateDeployment.created_at.desc())
        .limit(RECENT_LIMIT)
        .all()
    )
    return RunQueueResponse(
        active=_entry(active) if active else None,
        queued=[_entry(run, index) for index, run in enumerate(queued, start=1)],
        recent=[_entry(run) for run in recent],
    )


@router.delete("/{deployment_id}", operation_id="cancel_queued_run")
async def cancel_queued_run(
    deployment_id: UUID,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Remove a run from the queue before it starts. A run that has started is not stopped."""
    run = db.query(TemplateDeployment).filter_by(id=deployment_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not cancel_queued(db, run):
        raise HTTPException(status_code=400, detail=f"Only a queued run can be removed; this run is {run.status}")
    return {"id": str(run.id), "status": run.status, "message": f"{run.name} removed from the queue"}
