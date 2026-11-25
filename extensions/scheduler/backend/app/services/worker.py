# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The scheduler's worker: every TICK_SECONDS it follows the jobs that run and starts the ones that can.

A job that runs is followed by its Kubernetes job: completed, failed, or still running. A job asked to stop has its
Kubernetes job deleted, and is stopped once its pod is gone, which frees its GPUs.

The turn on a node: the waiting jobs that ask for a GPU start in order, the higher priorities first, then the older
ones. The first one that cannot start says what it waits for, and the ones behind it on that node wait behind it:
a small job does not take the GPU a larger one ahead of it is waiting for. A job that cannot start stops the
scheduler's running jobs of a lower priority that hold what it needs (placement.make_room), and no others; a job
stopped so waits again, and starts when its turn comes: where it stopped if it is resumable, from the beginning
otherwise. A job that asks for no GPU waits for no turn: Kubernetes places it by its memory and CPU.

A hold takes its turn as a job does, and has no pod here: given its GPUs, it holds them until its owner ends it.
When a job of a higher priority needs them, its owner is asked to give them up (app.services.owners), and the hold
waits again once the owner says they are free.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.jobs import COMPLETED, FAILED, HOLD, OPEN, RUNNING, STOPPED, STOPPING, WAITING, Job, NodeLimit
from app.services import placement
from app.services.nodes import NodesError
from app.services.owners import OwnerError
from app.services.pods import PodsError

logger = logging.getLogger(__name__)
TICK_SECONDS = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _end(job: Job, status: str, now: datetime, error: str = None) -> None:
    job.status, job.finished_at, job.gpu_ids, job.waiting, job.error = status, now, None, None, error


class Worker:
    def __init__(self, nodes, pods, owners) -> None:
        self.nodes, self.pods, self.owners = nodes, pods, owners

    async def tick(self, db: Session, now: datetime) -> None:
        jobs: List[Job] = db.query(Job).filter(Job.status.in_(OPEN)).all()
        names = {j.id: j.name for j in jobs}
        for job in jobs:
            if job.status in (RUNNING, STOPPING):
                await self._follow(job, now, names)
        waiting = sorted((j for j in jobs if j.status == WAITING), key=lambda j: (j.priority, j.created_at))
        nodes: Dict[str, Dict[str, Any]] = {}
        unread = None
        if any(j.gpus for j in waiting):
            try:
                nodes = {n["name"]: n for n in await self.nodes.list()}
            except NodesError as e:
                unread = str(e)
        for limit in db.query(NodeLimit).all():
            if limit.node in nodes and not nodes[limit.node].get("error"):
                nodes[limit.node]["gpus_at_work"] = limit.gpus_at_work
        # The first job of each node that could not start: the ones behind it wait behind it
        ahead: Dict[str, Job] = {}
        for job in waiting:
            if job.gpus and unread is not None:
                job.waiting = f"waiting for the nodes to be read: {unread}"
                continue
            if job.gpus and job.node in ahead:
                job.waiting = f"waiting behind job {ahead[job.node].name}, which is ahead of it on {job.node}"
                continue
            await self._place(job, nodes, jobs, now)
            if job.gpus and job.status == WAITING:
                ahead[job.node] = job
        db.commit()

    async def _follow(self, job: Job, now: datetime, names: Dict[str, str]) -> None:
        if job.kind == HOLD:
            # Its owner is asked at every tick until it says the GPUs are free (the hold's release)
            if job.status == STOPPING:
                needs = names.get(job.preempted_by, job.preempted_by)
                try:
                    await self.owners.ask_release(job.release_url, {"hold": job.id, "name": job.name, "for": needs})
                    job.waiting = f"its owner {job.owner} was asked to give its GPUs up for job {needs}"
                except OwnerError as e:
                    job.waiting = f"its owner {job.owner} could not be asked to give its GPUs up, trying again: {e}"
            return
        try:
            if job.status == STOPPING:
                await self.pods.stop(job.pod_job)
                if await self.pods.pod_gone(job.pod_job):
                    if job.preempted_by is None:
                        _end(job, STOPPED, now)
                    else:
                        # Stopped to make room: it waits again, in its place in the order
                        job.status, job.gpu_ids = WAITING, None
                        job.waiting = (f"stopped to make room for job "
                                       f"{names.get(job.preempted_by, job.preempted_by)}; waiting for its turn")
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
            stopping = [j for j in jobs if j.node == job.node and j.status == STOPPING and j.preempted_by == job.id]
            for other in ([] if stopping else placement.make_room(job, node, jobs, now)):
                other.status, other.preempted_by = STOPPING, job.id
                stopping.append(other)
            if stopping:
                reason = "making room: stopping " + ", ".join(f"job {j.name} (priority {j.priority})"
                                                             for j in stopping)
            job.waiting = reason
            return
        name = None
        if job.kind != HOLD:
            try:
                name = await self.pods.start(job, job.starts + 1, gpu_ids)
            except PodsError as e:
                job.waiting = f"it could not be started, trying again: {e}"
                return
        job.status, job.gpu_ids, job.pod_job, job.starts = RUNNING, gpu_ids, name, job.starts + 1
        job.started_at, job.waiting, job.error, job.preempted_by = now, None, None, None


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
