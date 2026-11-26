# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The runs of Mill tasks: starting one in this pod, and reading the record
of any run.

A run reports through NATS wherever it executes: this pod, the IDE, or a
pod on a GPU node. The consumer stores what it reports (app/services/store),
and this module reads that. The tasks repository is a folder of the shared
file system, mounted here and in the IDE.
"""

import collections
import datetime
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.runs import MillFailure, MillLog, MillRun

# What the library names the runner of the runs this pod starts
RUNNER = "mill"

# Seconds a new run has to send its first status or end
START_WAIT = 20.0

# The longest a description of a file's tasks may take
DESCRIBE_TIMEOUT = 60

# How many of the last lines a child process printed are kept, to say why it could not start
LINES_KEPT = 200


class RunsError(Exception):
    """An operation on runs that cannot be done; the message says why."""


class RunNotFound(RunsError):
    """No run has that id."""


class Child:
    """The process of a run this pod started, with the last lines it printed."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self.lines: Deque[str] = collections.deque(maxlen=LINES_KEPT)
        threading.Thread(target=self._drain, daemon=True, name="mill-child-output").start()

    def _drain(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))
        self.process.wait()


# The processes of the runs this pod started, by run id
_children: Dict[str, Child] = {}


def repository() -> Path:
    return Path(settings.MILL_REPOSITORY)


def _as_task_user() -> Dict[str, Any]:
    """What makes a child process run as the owner of the tasks repository, when this process is another user."""
    if os.geteuid() == settings.MILL_TASK_UID:
        return {}
    return {"user": settings.MILL_TASK_UID, "group": settings.MILL_TASK_GID}


# The environment variables Mill gives a task in this pod, which a secret's name may not take
GIVEN_ENV = ["PATH", "HOME", "PYTHONUNBUFFERED", "MILL_RUNNER", "MILL_NATS_URL", "MILL_SUBJECTS", "LLM_GATEWAY_URL",
             "THINKUBE_API_TOKEN", "NODE_NAME"]


def _task_env(token: Optional[str] = None, secrets: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """What a task's process is given. A task that calls a model reads the LLM Gateway's address as
    LLM_GATEWAY_URL and calls it with THINKUBE_API_TOKEN, the token of whoever started the run; each secret it
    asked for is a variable of its name. The token and the secrets are in the process's environment only, never in
    the run's record."""
    home = Path(settings.MILL_TASK_HOME)
    home.mkdir(parents=True, exist_ok=True)
    if os.geteuid() != settings.MILL_TASK_UID:
        os.chown(home, settings.MILL_TASK_UID, settings.MILL_TASK_GID)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "PYTHONUNBUFFERED": "1",
        "MILL_RUNNER": RUNNER,
        "MILL_NATS_URL": settings.MILL_NATS_URL,
        "MILL_SUBJECTS": settings.MILL_SUBJECTS,
        "LLM_GATEWAY_URL": settings.MILL_LLM_GATEWAY_URL,
    }
    if token:
        env["THINKUBE_API_TOKEN"] = token
    node = os.environ.get("NODE_NAME")
    if node:
        env["NODE_NAME"] = node
    env.update(secrets or {})
    return env


def _file_in_repository(file: str) -> Path:
    root = repository().resolve()
    path = (root / file).resolve()
    if root != path and root not in path.parents:
        raise RunsError(f"{file} is outside the tasks repository")
    if not path.is_file():
        raise RunsError(f"the tasks repository has no file {file}")
    return path


def _run(db: Session, run_id: str) -> MillRun:
    # Read again even when this session has read the run before: the consumer changes it
    run = db.query(MillRun).filter(MillRun.id == run_id).populate_existing().first()
    # A run whose first status has not arrived is still starting
    if run is None or run.status_seq == 0:
        raise RunNotFound(f"there is no run {run_id}")
    return run


def _summary(run: MillRun) -> Dict[str, Any]:
    info, status = run.info, run.status
    repo = info.get("repository") or {}
    return {
        "id": run.id,
        "task": info["task"],
        "file": info["file"],
        "limit": info.get("limit"),
        "host": info.get("host"),
        "node": info.get("node"),
        "runner": info.get("runner"),
        "commit": repo.get("commit"),
        "dirty": repo.get("dirty"),
        "tags": run.tags or {},
        "state": status["state"],
        "error": status["error"],
        "started_at": status["started_at"],
        "updated_at": status["updated_at"],
        "ended_at": status["ended_at"],
        "stages": status["stages"],
        "samples": status["samples"],
        "failures": status["failures"],
    }


def _reap() -> None:
    for run_id in [i for i, child in _children.items() if child.process.poll() is not None]:
        del _children[run_id]


TAG_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")
TAG_VALUE_LENGTH = 200


def _checked_tags(tags: Dict[str, str]) -> Dict[str, str]:
    for name, value in tags.items():
        if not TAG_NAME.match(name):
            raise RunsError(f"a tag's name is lowercase letters, digits, dots, hyphens and underscores, at most 40: {name!r}")
        if not isinstance(value, str) or len(value) > TAG_VALUE_LENGTH:
            raise RunsError(f"the value of the tag {name} is text of at most {TAG_VALUE_LENGTH} characters")
    return tags


def list_runs(db: Session, limit: int = 50, tags: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The newest runs, newest first; with tags, only the runs that have every one of them with that value."""
    _reap()
    started = db.query(MillRun).filter(MillRun.status_seq > 0)
    newest = started.order_by(MillRun.started_at.desc())
    if not tags:
        return {"runs": [_summary(run) for run in newest.limit(limit).populate_existing().all()], "total": started.count()}
    having = [run for run in newest.populate_existing().all()
              if all((run.tags or {}).get(name) == value for name, value in tags.items())]
    return {"runs": [_summary(run) for run in having[:limit]], "total": len(having)}


def tag_run(db: Session, run_id: str, tags: Dict[str, str]) -> Dict[str, Any]:
    """Attach tags to a run, wherever it executed. A tag given with an empty value is removed; the others stay."""
    run = _run(db, run_id)
    merged = {**(run.tags or {}), **_checked_tags(tags)}
    run.tags = {name: value for name, value in merged.items() if value != ""}
    db.commit()
    return {"id": run_id, "tags": run.tags}


def get_run(db: Session, run_id: str) -> Dict[str, Any]:
    """A run: what was run, and its status."""
    run = _run(db, run_id)
    return {
        "run": run.info,
        "status": run.status,
        "tags": run.tags or {},
        "can_stop": run.state == "running",
        "has_log": db.query(MillLog).filter(MillLog.run_id == run_id).first() is not None,
    }


def samples(db: Session, run_id: str) -> Dict[str, Any]:
    kept = _run(db, run_id).samples
    return kept if kept else {"latest": [], "spaced": []}


def failures(db: Session, run_id: str, limit: int = 50) -> Dict[str, Any]:
    run = _run(db, run_id)
    first = db.query(MillFailure).filter(MillFailure.run_id == run_id).order_by(MillFailure.n).limit(limit).all()
    return {
        "failures": [{"n": f.n, "at": f.at, "error": f.error, "fields": f.fields} for f in first],
        "total": run.status["failures"],
    }


def log(db: Session, run_id: str, lines: int = 200) -> Dict[str, Any]:
    """The last lines of what a run printed."""
    _run(db, run_id)
    pieces = db.query(MillLog).filter(MillLog.run_id == run_id).order_by(MillLog.seq).all()
    text = "".join(piece.text for piece in pieces).splitlines()
    return {"lines": text[-lines:], "total_lines": len(text)}


def describe(file: str) -> Dict[str, Any]:
    """The tasks a file of the repository declares."""
    path = _file_in_repository(file)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "thinkube_mill", "describe", str(path)],
            cwd=repository(), env=_task_env(), capture_output=True, text=True, timeout=DESCRIBE_TIMEOUT,
            **_as_task_user(),
        )
    except subprocess.TimeoutExpired:
        raise RunsError(f"reading the tasks of {file} took more than {DESCRIBE_TIMEOUT} seconds")
    if done.returncode != 0:
        raise RunsError(done.stderr.strip() or f"mill describe {file} ended with code {done.returncode}")
    return {"file": file, "tasks": json.loads(done.stdout)}


def start(
    db: Session,
    file: str,
    task: Optional[str],
    inputs: Dict[str, str],
    outputs: Dict[str, str],
    task_settings: Dict[str, Any],
    limit: Optional[int],
    token: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
    secrets: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Start a run of a task in this pod. Answers once the run's first status is stored.

    A path of an input or an output is relative to the root of the tasks
    repository, or absolute. `token` is the caller's token, which the task
    calls the platform's services with; `tags` are attached to the run;
    `secrets` are given to the task as environment variables of their names.
    """
    path = _file_in_repository(file)
    tags = _checked_tags(tags or {})
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{task or path.stem}-{secrets.token_hex(2)}"

    command = [sys.executable, "-m", "thinkube_mill", "run", f"{path}:{task}" if task else str(path)]
    for name, value in inputs.items():
        command += ["--in", f"{name}={value}"]
    for name, value in outputs.items():
        command += ["--out", f"{name}={value}"]
    for name, value in task_settings.items():
        command += ["--set", f"{name}={value if isinstance(value, str) else json.dumps(value)}"]
    if limit is not None:
        command += ["--limit", str(limit)]
    command += ["--run-id", run_id]

    child = Child(subprocess.Popen(
        command, cwd=repository(), env=_task_env(token, secrets), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        errors="replace", start_new_session=True, **_as_task_user(),
    ))
    _children[run_id] = child

    def stored() -> Optional[MillRun]:
        run = db.query(MillRun).filter(MillRun.id == run_id).populate_existing().first()
        if run is None or run.status_seq == 0:
            return None
        if tags and run.tags != tags:
            run.tags = tags
            db.commit()
        return run

    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        run = stored()
        if run is not None:
            return {"id": run_id, "state": run.state}
        if child.process.poll() is not None:
            break
        time.sleep(0.2)
    run = stored()
    if run is not None:
        return {"id": run_id, "state": run.state}
    if child.process.poll() is None:
        raise RunsError(
            f"run {run_id} is still starting: its first status did not reach the control center within "
            f"{int(START_WAIT)} seconds. It printed: " + (" / ".join(child.lines) or "nothing")
        )
    del _children[run_id]
    # Let the output thread take the last lines
    time.sleep(0.2)
    said = "\n".join(child.lines).strip()
    raise RunsError(said or f"the run ended with code {child.process.returncode} before it sent a status")
