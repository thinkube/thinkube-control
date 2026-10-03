# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
One deployment run at a time; the others wait in a queue.

Playbooks and template deploys change shared parts of the cluster (image
build directories, code-server, Keycloak clients, Harbor), so two running at
once can break each other. Every run is recorded as ``queued``. A single worker
starts the oldest queued run whenever no run is in flight, and the run's own
row reports its progress as before.

A run's requirements are checked when it is queued. If something changes
before it starts, the run fails with the error it meets.

The queue is the ``template_deployments`` table, so queued runs survive a
restart of thinkube-control and the worker picks them up again.
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.deployments import TemplateDeployment

logger = logging.getLogger(__name__)

QUEUED = "queued"
IN_FLIGHT = (QUEUED, "pending", "running")
FINISHED = ("success", "failed", "cancelled")

# Rows whose template_url starts with one of these run a playbook; any other
# row is a template deploy.
PLAYBOOK_SCHEMES = ("optional://", "core://")


class DuplicateRun(Exception):
    """The same run is already queued or in flight."""

    def __init__(self, existing: TemplateDeployment):
        self.existing = existing
        super().__init__(f"{existing.name} is already {existing.status}: {existing.id}")


def enqueue(db: Session, deployment: TemplateDeployment) -> int:
    """Record a run as queued and return its position (1 is the next to start).

    A run with the same name and address that is queued or in flight is not
    queued twice.
    """
    existing = (
        db.query(TemplateDeployment)
        .filter(
            TemplateDeployment.name == deployment.name,
            TemplateDeployment.template_url == deployment.template_url,
            TemplateDeployment.status.in_(IN_FLIGHT),
        )
        .first()
    )
    if existing is not None:
        raise DuplicateRun(existing)

    deployment.status = QUEUED
    db.add(deployment)
    db.commit()
    run_queue.wake()
    return position(db, deployment)


def position(db: Session, deployment: TemplateDeployment) -> Optional[int]:
    """The run's place among the queued runs, 1 being the next to start; None once it has started."""
    if deployment.status != QUEUED:
        return None
    queued = (
        db.query(TemplateDeployment)
        .filter(TemplateDeployment.status == QUEUED)
        .order_by(TemplateDeployment.created_at.asc())
        .all()
    )
    for index, row in enumerate(queued, start=1):
        if row.id == deployment.id:
            return index
    return None


def cancel_queued(db: Session, deployment: TemplateDeployment) -> bool:
    """Remove a run that has not started from the queue."""
    if deployment.status != QUEUED:
        return False
    deployment.status = "cancelled"
    deployment.output = "Removed from the queue before it started"
    db.commit()
    return True


class RunQueue:
    """Starts queued runs one at a time."""

    def __init__(self):
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def wake(self) -> None:
        """Look at the queue now instead of at the next interval."""
        self._wake.set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.start_next()
            except Exception as e:
                logger.error(f"Run queue: could not start the next run: {e}")

    async def start_next(self, db: Session = None) -> Optional[str]:
        """Start the oldest queued run if no run is in flight; return its id."""
        from app.services.background_executor import background_executor

        if background_executor.running_deployments:
            return None

        close_db = False
        if db is None:
            from app.db.session import SessionLocal
            db = SessionLocal()()
            close_db = True
        try:
            if db.query(TemplateDeployment).filter(TemplateDeployment.status.in_(["pending", "running"])).first():
                return None
            run = (
                db.query(TemplateDeployment)
                .filter(TemplateDeployment.status == QUEUED)
                .order_by(TemplateDeployment.created_at.asc())
                .first()
            )
            if run is None:
                return None

            run_id = str(run.id)
            variables = run.variables or {}
            run.status = "pending"
            db.commit()
            logger.info(f"Run queue: starting {run.name} ({run_id})")

            if run.template_url.startswith(PLAYBOOK_SCHEMES):
                await background_executor.execute_component_playbook(
                    run_id,
                    variables["playbook"],
                    dict(variables.get("parameters") or {}),
                    variables.get("component") or run.name,
                )
            else:
                await background_executor.start_deployment(run_id)
            return run_id
        finally:
            if close_db:
                db.close()


run_queue = RunQueue()
