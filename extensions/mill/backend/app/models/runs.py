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
    # The state of the latest status: running, completed, failed, stopped
    state = Column(String, nullable=False)
    # The seq of the status and of the samples kept, so an older message never replaces a newer one
    status_seq = Column(Integer, nullable=False)
    samples_seq = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    # When the stream stored the run's latest message, heartbeats included
    seen_at = Column(DateTime(timezone=True), nullable=False)


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
