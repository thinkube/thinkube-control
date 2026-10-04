# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Deployment runs interrupted by a restart of thinkube-control."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.deployments import TemplateDeployment

logger = logging.getLogger(__name__)

INTERRUPTED = "thinkube-control restarted while this run was in progress; start it again"


def mark_interrupted_runs(db: Session = None, still_running=None) -> int:
    """A deployment run left pending or running with no task behind it is marked failed.

    Playbook and template runs are tasks and child processes of the backend
    process, and they end with its pod while their rows keep saying running;
    the run queue starts nothing while such a row exists. At startup this
    process has started no run yet, so every such row belongs to a run that
    was interrupted. Later, ``still_running(run_id)`` says whether this
    process holds the run: one the old pod took during a rollout, after this
    pod had started, is caught that way.
    """
    close_db = False
    if db is None:
        from app.db.session import SessionLocal
        db = SessionLocal()()
        close_db = True
    try:
        stuck = (
            db.query(TemplateDeployment)
            .filter(TemplateDeployment.status.in_(["pending", "running"]))
            .all()
        )
        if still_running is not None:
            stuck = [run for run in stuck if not still_running(str(run.id))]
        for run in stuck:
            logger.warning(f"Deployment run {run.id} ({run.name}) was {run.status} when thinkube-control stopped; marked failed")
            run.status = "failed"
            run.output = INTERRUPTED
            run.completed_at = datetime.now(timezone.utc)
        if stuck:
            db.commit()
        return len(stuck)
    finally:
        if close_db:
            db.close()
