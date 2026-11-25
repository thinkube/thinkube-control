# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The scheduler's worker: every TICK_SECONDS it follows the jobs that run and starts the ones that can.

A job that runs is followed by its Kubernetes job: completed, failed, or still running. A job asked to stop has its
Kubernetes job deleted, and is stopped once its pod is gone, which frees its GPUs. A waiting job is started when its
node has the GPUs it asks for (app.services.placement), the jobs of a higher priority first, then the older ones;
a job that cannot start now says what it waits for and does not hold back the ones behind it.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.jobs import COMPLETED, FAILED, OPEN, RUNNING, STOPPED, STOPPING, WAITING, Job
from app.services import placement
from app.services.nodes import NodesError
from app.services.pods import PodsError

logger = logging.getLogger(__name__)
TICK_SECONDS = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _end(job: Job, status: str, now: datetime, error: str = None) -> None:
    job.status, job.finished_at, job.gpu_ids, job.waiting, job.error = status, now, None, None, error


class Worker:
    def __init__(self, nodes, pods) -> None:
        self.nodes, self.pods = nodes, pods

    async def tick(self, db: Session, now: datetime) -> None:
        jobs: List[Job] = db.query(Job).filter(Job.status.in_(OPEN)).all()
        for job in jobs:
            if job.status in (RUNNING, STOPPING):
                await self._follow(job, now)
        waiting = sorted((j for j in jobs if j.status == WAITING), key=lambda j: (j.priority, j.created_at))
        nodes: Dict[str, Dict[str, Any]] = {}
        unread = None
        if any(j.gpus for j in waiting):
            try:
                nodes = {n["name"]: n for n in await self.nodes.list()}
            except NodesError as e:
                unread = str(e)
        for job in waiting:
            if job.gpus and unread is not None:
                job.waiting = f"waiting for the nodes to be read: {unread}"
                continue
            await self._place(job, nodes, jobs, now)
        db.commit()

    async def _follow(self, job: Job, now: datetime) -> None:
        try:
            if job.status == STOPPING:
                await self.pods.stop(job.pod_job)
                if await self.pods.pod_gone(job.pod_job):
                    _end(job, STOPPED, now)
                return
            found = await self.pods.state(job.pod_job)
        except PodsError as e:
            job.waiting = f"its pod could not be read, trying again: {e}"
            return
        if found["state"] == "completed":
            _end(job, COMPLETED, now)
        elif found["state"] == "failed":
            _end(job, FAILED, now, found["error"])
        elif found["state"] == "gone":
            _end(job, FAILED, now, f"its Kubernetes job {job.pod_job} is gone, and the scheduler did not stop it")
        else:
            job.waiting = found.get("waiting")

    async def _place(self, job: Job, nodes: Dict[str, Dict[str, Any]], jobs: List[Job], now: datetime) -> None:
        node = nodes.get(job.node)
        if job.gpus and node is None:
            job.waiting = f"waiting for {job.node}: it has no node-metrics pod running, which says what its GPUs hold"
            return
        try:
            gpu_ids, reason = placement.place(job, node, jobs, now)
        except placement.CannotRun as e:
            _end(job, FAILED, now, str(e))
            return
        if gpu_ids is None:
            job.waiting = reason
            return
        try:
            name = await self.pods.start(job, job.starts + 1, gpu_ids)
        except PodsError as e:
            job.waiting = f"it could not be started, trying again: {e}"
            return
        job.status, job.gpu_ids, job.pod_job, job.starts = RUNNING, gpu_ids, name, job.starts + 1
        job.started_at, job.waiting, job.error = now, None, None


state: Dict[str, Any] = {"last_tick": None, "error": None}


async def run_forever(session_factory, worker: Worker) -> None:
    while True:
        db = session_factory()
        try:
            await worker.tick(db, utcnow())
            state["last_tick"], state["error"] = utcnow(), None
        except Exception as e:  # the loop goes on; what failed is said in the worker's state and the log
            db.rollback()
            state["error"] = f"{type(e).__name__}: {e}"
            logger.exception("the scheduler's tick failed")
        finally:
            db.close()
        await asyncio.sleep(TICK_SECONDS)
