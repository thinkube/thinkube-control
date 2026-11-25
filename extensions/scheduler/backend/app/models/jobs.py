# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A job: a command the scheduler runs in a pod on a node, on the GPUs it chooses. And a hold: GPUs the scheduler
keeps for work whose pod its owner starts, such as a model the LLM gateway serves."""

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, Text

from app.db.session import Base

# A job waits for its turn, runs, and ends: completed, failed, or stopped when asked. "stopping" is a running job
# that was asked to stop and has not ended yet.
WAITING, RUNNING, STOPPING = "waiting", "running", "stopping"
COMPLETED, FAILED, STOPPED = "completed", "failed", "stopped"
OPEN = (WAITING, RUNNING, STOPPING)
# The states in which a job holds its GPUs
HOLDING = (RUNNING, STOPPING)
# The scheduler runs a job's pod; a hold's pod is its owner's
JOB, HOLD = "job", "hold"
# The label of a hold's pod, whose value is the hold's id
HOLD_LABEL = "scheduler.thinkube.io/hold"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    kind = Column(String, nullable=False)
    name = Column(String, nullable=False)
    # The service the job is of ("forge", "mill"), or "person" for one a person gave
    owner = Column(String, nullable=False)
    created_by = Column(String, nullable=False)
    node = Column(String, nullable=False, index=True)
    # What runs, of a job: the image (a path in the cluster's registry), the command, its variables and its folder
    image = Column(String, nullable=True)
    command = Column(JSON, nullable=True)
    env = Column(JSON, nullable=False)
    working_dir = Column(String, nullable=True)
    # The folders it mounts, by the names of app.services.pods.MOUNTS
    mounts = Column(JSON, nullable=False)
    # What it needs: GPUs, the memory it takes on each (none: it has its GPUs alone), memory and CPU
    gpus = Column(Integer, nullable=False)
    gpu_memory_gb = Column(Float, nullable=True)
    memory_gb = Column(Integer, nullable=False)
    cpu_cores = Column(Integer, nullable=False)
    # Of a hold: the GPUs it must be given, when its pod is on them already; and the address its owner is asked at
    # to give its GPUs up, when work of a higher priority needs them
    on_gpus = Column(JSON, nullable=True)
    release_url = Column(String, nullable=True)
    # Of a hold: the namespace of its owner's pod, which carries the hold's id in the label HOLD_LABEL; and when
    # that pod was last there. A hold that names no namespace is ended by its owner alone
    pods_in = Column(String, nullable=True)
    pod_seen_at = Column(DateTime(timezone=True), nullable=True)
    # 1 (highest) to 5
    priority = Column(Integer, nullable=False)
    # Whether it goes on where it stopped when it is started again (a training with checkpoints), or starts over
    resumable = Column(Boolean, nullable=False)
    # Seconds it has to end after it is asked to stop
    stop_seconds = Column(Integer, nullable=False)

    status = Column(String, nullable=False, index=True)
    # The ids of the GPUs it was given, while it holds them
    gpu_ids = Column(JSON, nullable=True)
    # The Kubernetes job of its latest start, and how many times it was started
    pod_job = Column(String, nullable=True)
    starts = Column(Integer, nullable=False)
    # The id of the job it was stopped to make room for; it waits again and starts when its turn comes
    preempted_by = Column(String, nullable=True)
    # Why it waits, and why it failed
    waiting = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {c.name: (getattr(self, c.name).isoformat() if c.name.endswith("_at") and getattr(self, c.name)
                         else getattr(self, c.name))
                for c in self.__table__.columns if c.name != "env"}


class NodeLimit(Base):
    """A limit of a node that is not a priority: the most of its GPUs that hold work at once, such as a limit of
    heat. A node without a row has none."""

    __tablename__ = "node_limits"

    node = Column(String, primary_key=True)
    gpus_at_work = Column(Integer, nullable=False)
