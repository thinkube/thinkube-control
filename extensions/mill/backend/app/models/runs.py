# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The record of the runs, as the control center keeps it from the messages
the runs publish."""

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text

from app.db.session import Base


class MillRun(Base):
    """A run: what was run, its latest status and its latest samples."""

    __tablename__ = "mill_runs"

    id = Column(String, primary_key=True)
    # The run's "started" message: task, file, inputs, outputs, settings, commit, host
    info = Column(JSON, nullable=False)
    # The run's latest "status" message; none until the first arrives
    status = Column(JSON, nullable=True)
    # The run's latest "samples" message
    samples = Column(JSON, nullable=True)
    # What a person or an agent attached to the run to find it again and to link it with other work: names and
    # values, such as experiment=vote-of-finders. The run itself does not send them; the control center keeps them.
    tags = Column(JSON, nullable=True)
    # The state of the latest status: running, completed, failed, stopped
    state = Column(String, nullable=False)
    # The seq of the status and of the samples kept, so an older message never replaces a newer one
    status_seq = Column(Integer, nullable=False)
    samples_seq = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    # When the stream stored the run's latest message, heartbeats included
    seen_at = Column(DateTime(timezone=True), nullable=False)


class MillQueuedRun(Base):
    """A run asked of this pod while it ran as many runs as it may (settings.MILL_MAX_RUNS): it waits here and
    starts in order as earlier runs end. Once it starts, its row goes and the run's own record takes its place. The
    caller's token and the secrets' values are kept only in the memory of the pod that queued it."""

    __tablename__ = "mill_queued_runs"

    id = Column(String, primary_key=True)
    # What was asked: file, task, inputs, outputs, settings, limit, and the names of the secrets
    request = Column(JSON, nullable=False)
    tags = Column(JSON, nullable=True)
    # queued; stopped when it was cancelled or the pod restarted before it started; failed when it could not start
    state = Column(String, nullable=False)
    error = Column(Text, nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)


class MillFailure(Base):
    """An item a run reported as failed."""

    __tablename__ = "mill_failures"

    run_id = Column(String, primary_key=True)
    n = Column(Integer, primary_key=True)
    at = Column(String, nullable=False)
    error = Column(Text, nullable=False)
    fields = Column(JSON, nullable=False)


class MillLog(Base):
    """A piece of what a run printed."""

    __tablename__ = "mill_logs"

    run_id = Column(String, primary_key=True)
    seq = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)


class MillJob(Base):
    """A task run on a chosen node: a job Mill gave the Scheduler. Each start of the job is a run of its own,
    whose id begins with the job's (app.services.scheduled.run_prefix)."""

    __tablename__ = "mill_jobs"

    id = Column(String, primary_key=True)
    # The Scheduler's job
    scheduler_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    node = Column(String, nullable=False)
    # What was asked: the task (file, task, inputs, outputs, settings, limit, venv), its models' folders, its GPUs,
    # GPU memory, memory, CPU cores and priority
    request = Column(JSON, nullable=False)
    tags = Column(JSON, nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
