"""
Redeploy code-server from thinkube-control.

Redeploying code-server restarts its pod, so the playbook cannot run from
inside code-server: it would stop half way. thinkube-control runs it in its
own pod, where it keeps running while code-server restarts.
"""

from typing import Optional
from uuid import uuid4
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.deployments import TemplateDeployment

logger = logging.getLogger(__name__)
router = APIRouter(tags=["code-server"])

# Relative to the thinkube repository root, like optional component playbooks.
REDEPLOY_PLAYBOOK = "ansible/40_thinkube/core/code-server/20_redeploy.yaml"
REDEPLOY_URL = "core://code-server/redeploy"


class RedeployResponse(BaseModel):
    """A code-server redeploy run."""
    deployment_id: str
    status: str
    message: str
    # Place in the run queue, 1 being the next to start.
    queue_position: Optional[int] = None


class RedeployRunResponse(BaseModel):
    """The latest code-server redeploy run, if there is one."""
    deployment_id: Optional[str] = None
    status: Optional[str] = None
    output: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


def _latest_run(db: Session) -> Optional[TemplateDeployment]:
    return (
        db.query(TemplateDeployment)
        .filter(TemplateDeployment.template_url == REDEPLOY_URL)
        .order_by(TemplateDeployment.created_at.desc())
        .first()
    )


@router.post("/redeploy", response_model=RedeployResponse, operation_id="redeploy_code_server")
async def redeploy_code_server(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Queue a rebuild of the code-server image and a redeploy of code-server; answers at once with a deployment id.

    Runs start one at a time from the run queue. The redeploy takes place in
    thinkube-control, so it keeps going while the code-server pod restarts,
    and it leaves the workspace repositories untouched. Poll
    get_deployment_status with the id for the queue position, the step in
    progress and the outcome; get_deployment_logs has the full log. A second
    redeploy is refused while one is queued or in flight.
    """
    from app.services.run_queue import DuplicateRun, enqueue

    deployment = TemplateDeployment(
        id=uuid4(),
        name="redeploy-code-server",
        template_url=REDEPLOY_URL,
        variables={"playbook": REDEPLOY_PLAYBOOK, "component": "code-server"},
        created_by=current_user.get("preferred_username", "unknown"),
    )
    try:
        queue_position = enqueue(db, deployment)
    except DuplicateRun as duplicate:
        raise HTTPException(status_code=409, detail=str(duplicate))
    logger.info(f"code-server redeploy {deployment.id} queued at position {queue_position}")

    return RedeployResponse(
        deployment_id=str(deployment.id),
        status="queued",
        message=f"code-server redeploy queued at position {queue_position}; poll get_deployment_status with the deployment id",
        queue_position=queue_position,
    )


@router.get("/redeploy", response_model=RedeployRunResponse, operation_id="get_code_server_redeploy")
async def get_code_server_redeploy(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """The latest code-server redeploy run: its id, status and outcome, or empty fields if none ran."""
    latest = _latest_run(db)
    if latest is None:
        return RedeployRunResponse()
    return RedeployRunResponse(
        deployment_id=str(latest.id),
        status=latest.status,
        output=latest.output,
        created_at=latest.created_at.isoformat() if latest.created_at else None,
        completed_at=latest.completed_at.isoformat() if latest.completed_at else None,
    )
