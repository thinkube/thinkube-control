# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
News and fixes for the installed platform, from thinkube/thinkube-fixes.

The feed is checked when thinkube-control starts, every six hours, and on
request. Applying a fix queues a run that moves the thinkube checkout forward
to the release branch and runs the fixed component's playbook.
"""

import asyncio
import logging
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from kubernetes import client, config
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.deployments import TemplateDeployment
from app.models.fixes import NewsRead
from app.services import fixes_feed

logger = logging.getLogger(__name__)
router = APIRouter(tags=["fixes"])


class NewsEntry(BaseModel):
    id: str
    date: str
    title: str
    body: str
    read: bool


class FixEntry(BaseModel):
    id: str
    date: str
    title: str
    body: str
    severity: str
    component: str
    kind: str
    fixed_in: str
    commit: str
    playbook: str
    # applied, available, not_installed, or apply_from_ide
    status: str
    installed_version: Optional[str] = None
    # The command for Thinkube IDE, for a fix thinkube-control cannot apply itself.
    ide_command: Optional[str] = None


class FeedResponse(BaseModel):
    """News and fixes, with each fix's status on this cluster."""
    url: Optional[str] = None
    last_checked: Optional[str] = None
    # The failure of the last check; the entries are then from the last check that succeeded.
    last_error: Optional[str] = None
    feed_fetched: Optional[str] = None
    # The thinkube-fixes commit the entries were read from.
    feed_commit: Optional[str] = None
    news: List[NewsEntry]
    fixes: List[FixEntry]
    pending_fixes: int
    unread_news: int


class ApplyResponse(BaseModel):
    deployment_id: str
    status: str
    message: str
    queue_position: Optional[int] = None


def _installed_versions() -> dict:
    """Component name -> installed version; HTTP 502 when the cluster cannot be read."""
    try:
        config.load_incluster_config()
        return fixes_feed.installed_versions(client.CoreV1Api().list_config_map_for_all_namespaces)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cannot read the installed component versions: {e}")


def _view(db: Session) -> FeedResponse:
    return FeedResponse(**fixes_feed.feed_view(db, _installed_versions()))


@router.get("", response_model=FeedResponse, operation_id="list_news_and_fixes")
async def list_news_and_fixes(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """News and fixes for this platform release, with each fix's status on this cluster.

    A fix's status is applied (the component runs the fixed version or newer),
    available (apply_fix applies it), not_installed (the component is not on
    this cluster), or apply_from_ide (a fix of thinkube-control itself; run
    its ide_command in Thinkube IDE). last_error is the failure of the last
    check; the entries are then from the last check that succeeded.
    """
    return _view(db)


@router.post("/check", response_model=FeedResponse, operation_id="check_for_fixes")
async def check_for_fixes(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Read the news and fixes feed now, instead of waiting for the six-hourly check, and return it."""
    await asyncio.to_thread(fixes_feed.check_now, db)
    return _view(db)


@router.post("/{fix_id}/apply", response_model=ApplyResponse, operation_id="apply_fix")
async def apply_fix(
    fix_id: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Queue a fix; answers at once with a deployment id.

    The run moves the thinkube checkout forward to the platform's release
    branch, then runs the fixed component's playbook, which can restart that
    component. Poll get_deployment_status with the id for the queue position,
    the step in progress and the outcome; get_deployment_logs has the full log.
    Only a fix with status available can be applied.
    """
    from app.services.run_queue import DuplicateRun, enqueue

    fix = fixes_feed.find_fix(db, fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"No fix '{fix_id}' in the last feed read")
    status = fixes_feed.fix_status(fix, _installed_versions())
    if status == fixes_feed.APPLY_FROM_IDE:
        raise HTTPException(
            status_code=409,
            detail=f"{fix['component']} restarts thinkube-control; apply it from Thinkube IDE: {fixes_feed.ide_command(fix)}",
        )
    if status != fixes_feed.AVAILABLE:
        raise HTTPException(status_code=409, detail=f"Fix '{fix_id}' is {status}; only an available fix can be applied")

    deployment = TemplateDeployment(
        id=uuid4(),
        name=f"fix-{fix_id}",
        template_url=f"fix://{fix_id}",
        variables={
            "playbook": fix["playbook"],
            "component": fix["component"],
            "commit": fix["commit"],
            "fix_id": fix_id,
        },
        created_by=current_user.get("preferred_username", "unknown"),
    )
    try:
        queue_position = enqueue(db, deployment)
    except DuplicateRun as duplicate:
        raise HTTPException(status_code=409, detail=str(duplicate))
    logger.info(f"Fix {fix_id} queued as {deployment.id} at position {queue_position}")

    return ApplyResponse(
        deployment_id=str(deployment.id),
        status="queued",
        message=f"Fix {fix_id} queued at position {queue_position}; poll get_deployment_status with the deployment id",
        queue_position=queue_position,
    )


@router.post("/news/{news_id}/read", response_model=FeedResponse, operation_id="mark_news_read")
async def mark_news_read(
    news_id: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Mark a news entry read, so it no longer counts as new."""
    if db.query(NewsRead).filter(NewsRead.news_id == news_id).first() is None:
        db.add(NewsRead(news_id=news_id))
        db.commit()
    return _view(db)
