# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The runs' records in the database, written from the messages the runs
publish. The consumer calls this; the API reads the tables it fills."""

import datetime
import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.runs import MillFailure, MillLog, MillRun

log = logging.getLogger(__name__)

# What a message says besides its content
ENVELOPE = ("format", "run")

# Seconds without any message of a running run after which it is taken as
# lost. A run sends a heartbeat every 10 seconds, and tries a message again
# for 60 seconds while NATS cannot be reached.
LOST_AFTER = 90.0


def _content(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in ENVELOPE}


def _aware(moment: datetime.datetime) -> datetime.datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def apply(db: Session, kind: str, payload: Dict[str, Any], stored_at: datetime.datetime) -> None:
    """Store one message of a run. A message stored before changes nothing, so it may arrive again."""
    run_id = payload["run"]
    stored_at = _aware(stored_at)
    run = db.get(MillRun, run_id)
    if kind == "started":
        if run is None:
            content = _content(payload)
            db.add(MillRun(
                id=run_id, info=content, state="running", status_seq=0, samples_seq=0,
                started_at=datetime.datetime.fromisoformat(content["started_at"]), seen_at=stored_at,
            ))
            db.flush()
        return
    if run is None:
        # Every run sends "started" first and waits until the stream has it
        log.error("a %s message of run %s, which never sent its start; the message is left out", kind, run_id)
        return
    if stored_at > _aware(run.seen_at):
        run.seen_at = stored_at
    if kind == "status":
        if payload["seq"] > run.status_seq:
            content = _content(payload)
            del content["seq"]
            run.status = content
            run.state = content["state"]
            run.status_seq = payload["seq"]
    elif kind == "samples":
        if payload["seq"] > run.samples_seq:
            run.samples = {"latest": payload["latest"], "spaced": payload["spaced"]}
            run.samples_seq = payload["seq"]
    elif kind == "failure":
        if db.get(MillFailure, (run_id, payload["n"])) is None:
            db.add(MillFailure(run_id=run_id, n=payload["n"], at=payload["at"], error=payload["error"],
                               fields=payload["fields"]))
            db.flush()
    elif kind == "log":
        if db.get(MillLog, (run_id, payload["seq"])) is None:
            db.add(MillLog(run_id=run_id, seq=payload["seq"], text=payload["text"]))
            db.flush()
    elif kind != "alive":
        log.error("a message of run %s of the unknown kind %s; it is left out", run_id, kind)


def mark_lost(db: Session) -> List[str]:
    """Mark as failed the running runs that sent nothing for LOST_AFTER seconds. Returns their ids.

    Such a run's process ended without sending its last status: it was
    killed, or its pod stopped. Called only while every message of the
    stream is stored, so a run is not judged on messages still to be read.
    A status that arrives later replaces the mark.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    limit = now - datetime.timedelta(seconds=LOST_AFTER)
    marked = []
    lost = db.query(MillRun).filter(MillRun.state == "running", MillRun.seen_at < limit, MillRun.status_seq > 0)
    for run in lost.all():
        ended = now.isoformat(timespec="milliseconds")
        status = dict(run.status)
        status.update(
            state="failed",
            error=f"the run sent nothing for {int(LOST_AFTER)} seconds: its process ended without finishing it "
                  f"(host {run.info.get('host')})",
            ended_at=ended,
            updated_at=ended,
            stages=[{**stage, "ended_at": stage["ended_at"] or ended} for stage in status["stages"]],
        )
        run.status = status
        run.state = "failed"
        marked.append(run.id)
    return marked
