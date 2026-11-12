# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Fine-tuning runs, the training queue's slots, and the queue worker's own record."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, false

from app.db.session import Base


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if value else None


class Run(Base):
    """A fine-tuning of a base model on a dataset: a Meep² training job.

    `status` is the run's place in the training queue:

        preprocessing -> planning -> planned -> queued -> running -> completed
                                   ^        |  \\-> failed
                                   |        v
                                   +--- stopping -> paused

    and cancelled from any state that has not ended. A planning run waits for
    its node, then a short notebook job asks Meep² for its plan. A queued run
    waits for its node, and for a slot when `when` is "slots". A running run is
    a notebook job of Thinkube Control (`job_id`); stopping, it saves a
    checkpoint at the next step and becomes paused, by a slot's end
    (`paused_by` "slot"), by a person ("user"), or by a run of a higher
    priority ("preempted").
    """

    __tablename__ = "runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    # A dataset of the Datasets extension, the version the run trains on, and
    # what the run read of it: name, format, mapping and the file of the split
    # it trains on
    dataset_id = Column(String(36), nullable=False)
    dataset_version = Column(Integer, nullable=False)
    dataset = Column(JSON, nullable=True)
    # {"split": name} or {"holdout": share of the training split}, or null for none
    evaluation = Column(JSON, nullable=True)
    # Meep²'s milestones, or null for none: {"every": share of the planned steps, "eval": the evaluation set
    # (a version of a dataset of Datasets: its split's file and mapping, the columns that group its rows and hold
    # each source) or null, "metrics": what the scoring service computes on each group's answers}
    milestones = Column(JSON, nullable=True)
    base_model = Column(String(255), nullable=False)
    method = Column(String(16), nullable=False)
    node = Column(String(255), nullable=False)
    register = Column(Boolean, nullable=False)
    # The Meep² training job Forge decides: task, method, steps or epochs,
    # report_to, recipe and overrides; the notebook adds the model, the data
    # and the output folder, which are paths on the notebook's side
    job = Column(JSON, nullable=True)
    # The preprocessing of the run's data by Meep², on a CPU node before the plan: {"node", "job_id",
    # "key", "folder", "found", "report"}; the last three once its job has completed
    preprocess = Column(JSON, nullable=True)
    # Meep²'s plan from the plan job (Plan.to_dict()), and that job
    plan = Column(JSON, nullable=True)
    plan_job_id = Column(String(36), nullable=True)
    # The training's status file as last read: state, step, total_steps, loss,
    # seconds_per_step, eta_seconds, checkpoint, plan, error, updated
    progress = Column(JSON, nullable=True)
    # "now": as soon as the node is free; "slots": only inside the node's slots
    when = Column("run_when", String(8), nullable=False, server_default="now")
    across_slots = Column(Boolean, nullable=False, server_default=false())
    # Order among the node's waiting runs, lowest first
    position = Column(Integer, nullable=True)
    # Queue the run as soon as its plan is ready
    queue_on_plan = Column(Boolean, nullable=False, server_default=false())
    # 1 (highest) to 5 (lowest): work on the node with a higher priority makes
    # the run wait; work with a lower one is stopped to make room for it
    priority = Column(Integer, nullable=False, server_default="3")
    # "slot", "user", or "preempted" by preempted_by, a run of a higher priority
    paused_by = Column(String(16), nullable=True)
    preempted_by = Column(String(255), nullable=True)
    # Why the worker has not started the run yet, as it last decided
    waiting = Column(Text, nullable=True)
    stop_requested_at = Column(DateTime(timezone=True), nullable=True)
    # The person's choice for the job's memory and CPU cores, under Advanced; when
    # null, Forge sizes the memory (runs.job_request) and the node's default gives the cores
    memory_gb = Column(Integer, nullable=True)
    cpu_cores = Column(Integer, nullable=True)
    # The GGUF export of the registered model: {"model_id", "quantization",
    # "node", "job_id", "status", "error"}, or null before one is asked for
    export = Column(JSON, nullable=True)
    # The NVFP4 export: {"model_id", "calibration_rows", "node", "job_id", "status", "error"}, or null
    nvfp4 = Column(JSON, nullable=True)
    model_id = Column(String(255), nullable=True)
    job_id = Column(String(36), nullable=True)
    status = Column(String(32), nullable=False)
    error = Column(Text, nullable=True)
    # The MLflow run the training logs to; its artifacts hold the run's plan,
    # status and stop files (app/services/runfiles.py)
    mlflow_experiment_id = Column(String(64), nullable=True)
    mlflow_run_id = Column(String(64), nullable=True)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "dataset": self.dataset,
            "evaluation": self.evaluation,
            "milestones": self.milestones,
            "base_model": self.base_model,
            "method": self.method,
            "node": self.node,
            "register": self.register,
            "job_spec": self.job,
            "preprocess": self.preprocess,
            "plan": self.plan,
            "plan_job_id": self.plan_job_id,
            "progress": self.progress,
            "when": self.when,
            "across_slots": self.across_slots,
            "position": self.position,
            "queue_on_plan": self.queue_on_plan,
            "priority": self.priority,
            "paused_by": self.paused_by,
            "preempted_by": self.preempted_by,
            "waiting": self.waiting,
            "stop_requested_at": _iso(self.stop_requested_at),
            "memory_gb": self.memory_gb,
            "cpu_cores": self.cpu_cores,
            "export": self.export,
            "nvfp4": self.nvfp4,
            "model_id": self.model_id,
            "job_id": self.job_id,
            "status": self.status,
            "error": self.error,
            "mlflow_run_id": self.mlflow_run_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
        }


class Slot(Base):
    """A node and a recurring window in which it trains: the days of the week
    the window starts on (0 Monday to 6 Sunday), its start and end as HH:MM in
    its time zone. An end at or before the start ends the next day, so a
    window may cross midnight; an end equal to the start is a whole day."""

    __tablename__ = "slots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    node = Column(String(255), nullable=False)
    days = Column(JSON, nullable=False)
    start = Column(String(5), nullable=False)
    end = Column(String(5), nullable=False)
    timezone = Column(String(64), nullable=False)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "node": self.node,
            "days": self.days,
            "start": self.start,
            "end": self.end,
            "timezone": self.timezone,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


class NodeState(Base):
    """What the queue worker holds on a node: training mode, which it turned
    on for a slot, and the end of that slot; and its last note about the node,
    such as why a slot waits."""

    __tablename__ = "queue_nodes"

    node = Column(String(255), primary_key=True)
    training_mode = Column(Boolean, nullable=False, server_default=false())
    # "slot" or "run": what the worker holds training mode for
    held_for = Column(String(8), nullable=True)
    slot_end = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    def to_dict(self):
        return {"node": self.node, "training_mode": self.training_mode, "held_for": self.held_for, "slot_end": _iso(self.slot_end),
                "note": self.note, "updated_at": _iso(self.updated_at)}


class Priority(Base):
    """The priority of a kind of work on a GPU node that has none of its own,
    and the priority a new Forge run gets: 1 (highest) to 5 (lowest)."""

    __tablename__ = "priorities"

    kind = Column(String(32), primary_key=True)
    priority = Column(Integer, nullable=False)


class WorkerCredential(Base):
    """The Thinkube Control API token the queue worker calls Control with.

    A person creates it from Forge (POST /worker/connect): Forge asks Control
    for an API token of that person, with the person's own session, and keeps
    it here. The worker then acts with that person's rights."""

    __tablename__ = "worker_credentials"

    id = Column(String(16), primary_key=True)
    token = Column(Text, nullable=False)
    token_id = Column(String(64), nullable=False)
    username = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
