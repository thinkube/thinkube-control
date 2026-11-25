# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The training queue's worker: a background task of Forge's backend.

Every TICK_SECONDS it reads the queue from the database, follows the jobs of the runs, and decides what each node
does next. It keeps nothing in memory between ticks: after a restart (a push replaces Forge's pod) the next tick
finds the running runs by their job ids and their stop requests by stop_requested_at, and goes on from there.

A run's jobs (its preprocessing, plan and training) are jobs of the Scheduler (app/services/scheduled.py), which
decides when each one runs among all the work on its node: notebook servers, served models, apps and the other
parts of the platform. Forge decides only among its own runs:

- Priorities (app/services/priorities.py): a run has a priority, 1 (highest) to 5 (lowest), which its jobs carry
  to the Scheduler. Two of Forge's runs never hold a node's GPUs at once: a run waits for Forge's work of a higher
  or the same priority on its node, and a run of a lower priority is paused at its next step, with a checkpoint
  (its stop file), to make room. A run whose job the Scheduler stopped for work of a higher priority outside Forge
  stays running, and its training goes on from its last checkpoint when its turn comes again; its waiting line
  says so.
- A node's candidate is its first run that may start now, by priority, then plans before trainings, then
  position. A queued run "now" may start at any time. A run "slots" may start only inside the node's slot, and
  only when its remaining time (its status file, else its plan's estimate) fits before the slot ends, unless it is
  across_slots. When a slot ends, every running "slots" run of the node gets its stop file, and one whose next
  milestone falls due before the slot's end and would end after it gets it before that milestone (Meep² runs a
  milestone before a stop at its step takes effect). A paused run starts again by itself when a slot or a run of a
  higher priority paused it; a person's pause holds it until the person resumes it. A run that has not paused
  STOP_GRACE_SECONDS, and the time of one milestone, after its stop file was put is cancelled and recorded as
  failed.
- A task of Mill is not Forge's: Mill gives it to the Scheduler itself. A task run Forge kept from before was
  ended by the migration c5e7a9b1d3f4.
- A failed run is recorded with its error, and the queue goes on. A call that fails with no answer, or with a
  server error, is tried again at the next tick; a refusal fails the run.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.forge import Run, Slot, WorkerCredential
from app.services import control, priorities, runfiles, runs, slots as slot_windows
from app.services.priorities import Occupant

logger = logging.getLogger(__name__)

TICK_SECONDS = 15
STOP_GRACE_SECONDS = 30 * 60
# The stop before a milestone is asked for this long before the step ahead of it ends: a tick, and the status
# file's own delay of about one more
MILESTONE_LEAD_SECONDS = 3 * TICK_SECONDS
# The worker's advisory lock: while a new pod starts beside the old one, only one of them ticks
LOCK_KEY = 0x466F726765
CREDENTIAL_ID = "control"
# A paused run that starts again by itself
RESUMES_BY_ITSELF = ("slot", "preempted")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def raised(job: Dict[str, Any]) -> str:
    """What a job's failed cell raised, as the cell's own error says (Meep²'s refusal of a plan, for
    one); Control's error when no cell output names it."""
    for result in job.get("results") or []:
        for output in result.get("outputs") or []:
            if output.get("output_type") == "error":
                return f"{output.get('ename')}: {output.get('evalue')}"
    return job.get("error") or "no error given"


def transient(e: control.ControlError) -> bool:
    """A failure that can clear on its own: no answer, or a server error."""
    return e.status >= 500


class ControlPort:
    """Thinkube Control as the worker calls it, with the worker's token."""

    def __init__(self, token: str):
        self.token = token

    async def catalog(self) -> Dict[str, Any]:
        return await control.catalog(self.token)

    async def run_job(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Give a run's job to the Scheduler; its script calls Control with this token."""
        return await control.run_job(self.token, request)

    async def node_choices(self, node: str) -> Dict[str, List[int]]:
        return await control.node_choices(self.token, node)

    async def nodes_choices(self) -> Dict[str, Dict[str, List[int]]]:
        return await control.nodes_choices(self.token)

    async def job(self, job_id: str) -> Dict[str, Any]:
        return await control.job(self.token, job_id)

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        return await control.cancel_job(self.token, job_id)


class Worker:
    def __init__(self, control_port, files, clock: Callable[[], datetime] = utcnow):
        self.control = control_port
        self.files = files
        self.clock = clock

    # --- one tick ----------------------------------------------------------

    async def tick(self, db: Session) -> None:
        now = self.clock()
        open_runs = db.query(Run).filter(Run.kind != "task", Run.status.in_(runs.BEFORE_PLAN + runs.WAITING
                                                                             + runs.TRAINING)).all()
        for run in open_runs:
            # A run whose job is followed says, this tick, what the job waits for
            run.waiting = None
        await self._follow(db)
        by_node = self._slots_by_node(db)
        nodes = sorted(set(by_node) | {r.node for r in open_runs})
        windows = {node: slot_windows.active(by_node.get(node, []), now) for node in nodes}
        await self._end_slots(db, windows)
        for node in nodes:
            candidate = self._candidate(db, node, windows[node], now)
            if candidate is not None:
                await self._place(db, node, candidate, now)
        db.commit()

    # --- following jobs ----------------------------------------------------

    async def _follow(self, db: Session) -> None:
        for run in db.query(Run).filter(Run.status == "preprocessing").all():
            if (run.preprocess or {}).get("job_id") is None:
                await self._start_preprocess(db, run)
            else:
                await self._follow_preprocess(db, run)
        for run in db.query(Run).filter(Run.status == "revising").all():
            if run.plan_job_id is None:
                await self._start_revise(db, run)
            else:
                await self._follow_revise(db, run)
        for run in db.query(Run).filter(Run.status == "planning",
                                        Run.plan_job_id.isnot(None)).all():
            await self._follow_plan(db, run)
        for run in db.query(Run).filter(Run.kind != "task", Run.status.in_(runs.TRAINING)).all():
            await self._follow_training(db, run)
        db.commit()

    async def _job(self, run: Run, job_id: str) -> Optional[Dict[str, Any]]:
        """The run's job at the Scheduler; while it waits, the run says what for."""
        try:
            job = await self.control.job(job_id)
        except control.ControlError as e:
            logger.warning("run %s: job %s could not be read, asking again at the next tick: %s", run.id, job_id, e)
            return None
        if job.get("waiting") and job["status"] in runs.JOB_OPEN:
            run.waiting = job["waiting"]
        return job

    async def _start_preprocess(self, db: Session, run: Run) -> None:
        """Start the preprocessing of the run's data on a CPU node; it needs no GPU and waits for nothing."""
        try:
            nodes = await self.control.nodes_choices()
        except control.ControlError as e:
            run.error = f"waiting for Control: {e.detail}"
            return
        node = runs.preprocess_node(nodes, run.node)
        try:
            request = runs.job_request(run, None, "preprocess", nodes[node], node=node)
        except ValueError as e:
            self._fail(run, str(e))
            return
        try:
            job = await self.control.run_job(request)
        except control.ControlError as e:
            if transient(e):
                run.error = f"waiting for Control: {e.detail}"
                return
            self._fail(run, e.detail)
            return
        run.preprocess = {"node": node, "job_id": job["job_id"]}
        run.error = None
        db.commit()

    async def _follow_preprocess(self, db: Session, run: Run) -> None:
        job = await self._job(run, run.preprocess["job_id"])
        if job is None or job["status"] in runs.JOB_OPEN:
            return
        if job["status"] != "completed":
            self._fail(run, f"Meep²'s preprocessing failed: {raised(job)}" if job["status"] == "error"
                       else f"the preprocessing job {job['status']}: {job.get('error') or 'no error given'}")
            return
        try:
            outcome = await self.files.preprocess(run)
        except runfiles.RunFilesError as e:
            logger.warning("run %s: its preprocessing could not be read, asking again at the next tick: %s", run.id, e)
            return
        if outcome is None:
            self._fail(run, "the preprocessing job completed and wrote no outcome")
            return
        run.preprocess = {**run.preprocess, **outcome}
        run.error = None
        # The plan reuses an earlier plan's measurements when one may hold: the run's own last plan on its node,
        # else another run's of the same model, method and task on it; Meep² decides, and the run measures when not
        if not run.measure and run.reuse is not None and run.reuse.get("node") != run.node:
            run.reuse = None
        if not run.measure and run.reuse is None:
            run.reuse = runs.other_plan(run, db.query(Run).filter(Run.node == run.node,
                                                                  Run.base_model == run.base_model).all())
        if not run.measure and run.reuse is not None and run.reuse.get("plan"):
            run.status, run.revises, run.plan_job_id = "revising", run.reuse["job_id"], None
        else:
            run.status = "planning"

    async def _start_revise(self, db: Session, run: Run) -> None:
        """Start the plan of the run from an earlier plan's measurements (run.reuse) where its data was preprocessed:
        Meep² loads no model and measures nothing, so the job needs no GPU and waits for nothing."""
        try:
            nodes = await self.control.nodes_choices()
        except control.ControlError as e:
            run.error = f"waiting for Control: {e.detail}"
            return
        node = run.preprocess["node"]
        try:
            request = runs.job_request(run, None, "revise", nodes[node], node=node)
        except ValueError as e:
            self._fail(run, str(e))
            return
        try:
            job = await self.control.run_job(request)
        except control.ControlError as e:
            if transient(e):
                run.error = f"waiting for Control: {e.detail}"
                return
            self._fail(run, e.detail)
            return
        run.plan_job_id, run.error = job["job_id"], None
        db.commit()

    async def _follow_revise(self, db: Session, run: Run) -> None:
        """The revision's outcome: the plan, or Meep²'s reason the measurements do not hold, and the run plans on
        its node's GPU, measuring."""
        job = await self._job(run, run.plan_job_id)
        if job is None or job["status"] in runs.JOB_OPEN:
            return
        if job["status"] != "completed":
            self._fail(run, f"Meep²'s plan from earlier measurements failed: {raised(job)}" if job["status"] == "error"
                       else f"the revision job {job['status']}: {job.get('error') or 'no error given'}")
            return
        try:
            outcome = await self.files.revision(run)
        except runfiles.RunFilesError as e:
            logger.warning("run %s: its revision could not be read, asking again at the next tick: %s", run.id, e)
            return
        if outcome is None:
            self._fail(run, "the revision job completed and wrote no outcome")
            return
        if "measure_again" in outcome:
            run.reuse = {**run.reuse, "measure_again": outcome["measure_again"]}
            run.status, run.plan_job_id, run.revises = "planning", None, None
            return
        self._planned(db, run, outcome["plan"])

    async def _follow_plan(self, db: Session, run: Run) -> None:
        job = await self._job(run, run.plan_job_id)
        if job is None or job["status"] in runs.JOB_OPEN:
            return
        if job["status"] != "completed":
            self._fail(run, f"Meep²'s plan failed: {raised(job)}" if job["status"] == "error"
                       else f"the plan job {job['status']}: {job.get('error') or 'no error given'}")
            return
        try:
            plan = await self.files.plan(run)
        except runfiles.RunFilesError as e:
            logger.warning("run %s: its plan could not be read, asking again at the next tick: %s", run.id, e)
            return
        if plan is None:
            self._fail(run, "the plan job completed and wrote no plan")
            return
        self._planned(db, run, plan)

    def _planned(self, db: Session, run: Run, plan: Dict[str, Any]) -> None:
        """The run with its plan: queued when it was asked to be, else planned."""
        run.plan = plan
        run.error = None
        if run.queue_on_plan and runs.sized(run):
            enqueue(db, run)
        else:
            run.status = "planned"
            if not runs.sized(run):
                run.error = (f"not queued: Meep²'s plan does not size its training job ({runs.memory_reason(plan)}); "
                             "set its memory under Advanced")

    async def _follow_training(self, db: Session, run: Run) -> None:
        job = await self._job(run, run.job_id)
        if job is None:
            return
        try:
            status = await self.files.status(run)
        except runfiles.RunFilesError as e:
            logger.warning("run %s: its status file could not be read: %s", run.id, e)
            status = None
        if status is not None:
            run.progress = status
        if job["status"] in runs.JOB_OPEN:
            await self._stop_overdue(run)
            return
        state = (status or {}).get("state")
        now = self.clock()
        if job["status"] == "completed" and state == "completed":
            run.status, run.finished_at, run.error, run.position = "completed", now, None, None
        elif job["status"] == "completed" and state == "paused":
            run.status = "paused"
            run.paused_by = run.paused_by or "user"
            run.stop_requested_at = None
            if run.paused_by != "preempted":
                run.preempted_by = None
        elif job["status"] == "cancelled":
            if run.status != "cancelled":
                run.status, run.finished_at, run.position = "cancelled", now, None
                run.error = job.get("error")
        elif job["status"] == "completed":
            self._fail(run, f"the training's job completed, and Meep²'s status file says {state!r}")
        else:
            reason = (status or {}).get("error") or job.get("error") or "no error given"
            self._fail(run, f"the training's job {job['status']}: {reason}")

    async def _stop_overdue(self, run: Run) -> None:
        """A run asked to stop that has not paused within STOP_GRACE_SECONDS, and the time of a milestone, which
        runs before a stop takes effect, is cancelled."""
        if run.status != "stopping" or run.stop_requested_at is None:
            return
        grace = STOP_GRACE_SECONDS + runs.milestone_seconds(run)
        if self.clock() - run.stop_requested_at < timedelta(seconds=grace):
            return
        try:
            await self.control.cancel_job(run.job_id)
        except control.ControlError as e:
            logger.warning("run %s: its job could not be cancelled, trying again at the next tick: %s", run.id, e)
            return
        self._fail(run, f"it did not pause within {grace // 60:.0f} minutes of its stop request; "
                        "its job was cancelled")

    def _fail(self, run: Run, error: str) -> None:
        run.status, run.error, run.finished_at = "failed", error, self.clock()
        run.position = None
        run.stop_requested_at = None

    # --- slots ---------------------------------------------------------------

    @staticmethod
    def _slots_by_node(db: Session) -> Dict[str, List[Slot]]:
        found: Dict[str, List[Slot]] = {}
        for s in db.query(Slot).all():
            found.setdefault(s.node, []).append(s)
        return found

    async def _end_slots(self, db: Session, windows) -> None:
        """Pause the "slots" runs of nodes whose slot has ended, and those whose next milestone would end after
        the slot."""
        for run in db.query(Run).filter(Run.kind != "task", Run.when == "slots", Run.status == "running").all():
            window = windows.get(run.node)
            if window is None:
                await self._request_stop(run, "slot", self.clock())
            elif runs.stops_before_milestone(run, window.end, self.clock(), MILESTONE_LEAD_SECONDS):
                await self._request_stop(run, "slot", self.clock())
        db.flush()

    async def _request_stop(self, run: Run, by: str, now: datetime) -> bool:
        try:
            await self.files.request_stop(run)
        except runfiles.RunFilesError as e:
            run.error = f"its stop file could not be put, trying again: {e}"
            return False
        run.status, run.paused_by, run.stop_requested_at = "stopping", by, now
        return True

    # --- deciding what a node does ------------------------------------------

    @staticmethod
    def _forge_work(db: Session, node: str) -> List[Run]:
        """The node's Forge runs that hold it: training or stopping, or planning in a job."""
        return db.query(Run).filter(
            Run.node == node, Run.kind != "task",
            (Run.status.in_(runs.TRAINING)) | ((Run.status == "planning") & Run.plan_job_id.isnot(None))).all()

    def _candidate(self, db: Session, node: str, window, now: datetime) -> Optional[Run]:
        """The node's first run that may start now: by priority, plans before trainings, then position."""
        found = []
        for run in db.query(Run).filter(Run.node == node, Run.kind != "task",
                                        Run.status.in_(("planning",) + runs.WAITING)).all():
            if run.status == "planning":
                if run.plan_job_id is None:
                    found.append((run.priority, 0, 0, run.created_at, run))
                continue
            if run.status == "paused" and run.paused_by not in RESUMES_BY_ITSELF:
                continue
            if run.when == "slots":
                if window is None:
                    continue
                if not run.across_slots:
                    try:
                        needed = runs.remaining_seconds(run)
                    except ValueError as e:
                        self._fail(run, str(e))
                        continue
                    if needed > (window.end - now).total_seconds():
                        run.waiting = "waiting for a slot long enough for its time left"
                        continue
            found.append((run.priority, 1, run.position if run.position is not None else 0, run.created_at, run))
        return min(found, key=lambda f: f[:4])[4] if found else None

    async def _place(self, db: Session, node: str, run: Run, now: datetime) -> None:
        """Start the node's candidate, or pause Forge's runs of a lower priority for it, or say which of Forge's
        runs it waits for. The work outside Forge is the Scheduler's to weigh."""
        occupants = [Occupant("forge_run", r.name if r.status != "planning" else f"{r.name} (its plan)", r.priority, r)
                     for r in self._forge_work(db, node) if r.id != run.id]
        waits, stops = priorities.decide(run.priority, occupants)
        if waits:
            run.waiting = priorities.waiting_for(waits[0], node)
            return
        if stops:
            await self._make_room(run, stops, now)
            return
        await self._start(db, run, "plan" if run.status == "planning" else "train")

    async def _make_room(self, run: Run, stops: List[Occupant], now: datetime) -> None:
        """Pause Forge's runs of a lower priority: a training at its next step, with a checkpoint; a plan is
    stopped, and plans again later."""
        stopping = []
        for o in stops:
            try:
                if o.kind == "forge_run":
                    other: Run = o.ref
                    if other.status == "running" and await self._request_stop(other, "preempted", now):
                        other.preempted_by = run.name
                    elif other.status == "planning":
                        # A plan job has no checkpoint: it is cancelled, and plans again later
                        await self.control.cancel_job(other.plan_job_id)
                        other.plan_job_id = None
            except control.ControlError as e:
                run.error = f"{o.label} {o.name} could not be stopped, trying again: {e.detail}"
            stopping.append(f"{o.label} {o.name} (priority {o.priority})")
        run.waiting = "making room: stopping " + ", ".join(stopping)

    async def _start(self, db: Session, run: Run, mode: str) -> bool:
        """Start the run's plan job or training job; True when the Scheduler took it."""
        try:
            catalog = (await self.control.catalog())["models"]
            choices = await self.control.node_choices(run.node)
        except control.ControlError as e:
            run.error = f"waiting for Control: {e.detail}"
            return False
        base = next((m for m in catalog if m["id"] == run.base_model), None)
        if base is None:
            self._fail(run, f"the base model {run.base_model} is no longer in the catalogue")
            return False
        # A training job resumes from the checkpoints its output folder holds (see the notebook's Train cell);
        # a stop file left by an earlier pause would stop it at its first step
        if mode == "train":
            try:
                await self.files.clear_stop(run)
            except runfiles.RunFilesError as e:
                run.error = f"its stop file could not be removed, trying again: {e}"
                return False
        try:
            request = runs.job_request(run, base, mode, choices)
        except ValueError as e:
            self._fail(run, str(e))
            return False
        try:
            job = await self.control.run_job(request)
        except control.ControlError as e:
            if transient(e):
                run.error = f"waiting for Control: {e.detail}"
                return False
            self._fail(run, e.detail)
            return False
        if mode == "plan":
            run.plan_job_id = job["job_id"]
        else:
            run.job_id = job["job_id"]
            run.status = "running"
            run.started_at = self.clock()
            run.paused_by = None
            run.preempted_by = None
            run.stop_requested_at = None
        run.error, run.waiting = None, None
        db.commit()
        return True


def enqueue(db: Session, run: Run) -> None:
    """Put a run at the end of its node's queue."""
    last = db.query(Run.position).filter(Run.node == run.node, Run.status.in_(runs.WAITING),
                                         Run.position.isnot(None)).order_by(Run.position.desc()).first()
    run.position = (last[0] if last else 0) + 1
    run.status = "queued"


def credential(db: Session) -> Optional[WorkerCredential]:
    return db.get(WorkerCredential, CREDENTIAL_ID)


# --- the background task ------------------------------------------------

state: Dict[str, Any] = {"last_tick": None, "error": None}


async def tick_once(session_factory) -> None:
    db = session_factory()
    # The advisory lock belongs to one database connection: it is taken and released on a connection of its own,
    # since the session hands its connection back to the pool at every commit and rollback
    lock = db.get_bind().connect()
    try:
        locked = lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        lock.commit()
        if not locked:
            state["error"] = "another Forge pod's worker holds the queue"
            return
        try:
            found = credential(db)
            if found is None:
                state["error"] = "the queue has no Control token; connect it on Forge's Slots page"
                return
            worker = Worker(ControlPort(found.token), runfiles.RunFiles())
            await worker.tick(db)
            state["last_tick"], state["error"] = utcnow().isoformat(), None
        finally:
            db.rollback()
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock.commit()
    finally:
        lock.close()
        db.close()


async def run_forever(session_factory) -> None:
    while True:
        try:
            await tick_once(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # the worker records what failed and ticks again
            logger.exception("the queue worker's tick failed")
            state["error"] = f"the last tick failed: {e}"
        await asyncio.sleep(TICK_SECONDS)
