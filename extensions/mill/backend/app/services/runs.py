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
from app.db.session import get_session_local
from app.models.runs import MillFailure, MillLog, MillQueuedRun, MillRun

# What the library names the runner of the runs this pod starts
RUNNER = "mill"

# Seconds a new run has to send its first status or end
START_WAIT = 20.0

# The longest a description of a file's tasks may take
DESCRIBE_TIMEOUT = 60

# How many of the last lines a child process printed are kept, to say why it could not start
LINES_KEPT = 200

# Seconds between two looks at the queue for a run that can start
DISPATCH_SECONDS = 1.0

# Why a queued run that was left waiting when the pod restarted cannot start
RESTARTED = ("Mill's main pod restarted before this run started. A queued run's token and secrets are kept only in "
             "the memory of the pod that queued it, so it cannot start: start it again")


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
    """The newest runs, newest first, the queued ones among them; with tags, only the runs that have every one of
    them with that value. A queued run has its position in the queue; the others have none."""
    _reap()
    started = db.query(MillRun).filter(MillRun.status_seq > 0)
    newest = started.order_by(MillRun.started_at.desc())
    rows = db.query(MillQueuedRun).order_by(MillQueuedRun.queued_at.desc()).populate_existing().all()
    # A queued run that has just started has its own record; its row goes a moment later
    begun = {run_id for (run_id,) in started.filter(MillRun.id.in_([row.id for row in rows])).with_entities(MillRun.id)}
    positions = _positions(db)
    waiting = [_queued_summary(row, positions) for row in rows if row.id not in begun]
    if not tags:
        ran = [{**_summary(run), "position": None} for run in newest.limit(limit).populate_existing().all()]
        every = sorted(waiting + ran, key=lambda r: r["started_at"], reverse=True)
        return {"runs": every[:limit], "total": started.count() + len(waiting)}
    ran = [{**_summary(run), "position": None} for run in newest.populate_existing().all()]
    having = [r for r in sorted(waiting + ran, key=lambda r: r["started_at"], reverse=True)
              if all(r["tags"].get(name) == value for name, value in tags.items())]
    return {"runs": having[:limit], "total": len(having)}


def tag_run(db: Session, run_id: str, tags: Dict[str, str]) -> Dict[str, Any]:
    """Attach tags to a run, wherever it executed, or to a run in the queue. A tag given with an empty value is
    removed; the others stay."""
    checked = _checked_tags(tags)
    try:
        run = _run(db, run_id)
    except RunNotFound:
        queue = _queue_session()
        try:
            row = _queue_row(queue, run_id)
            if row is None:
                raise
            merged = {**(row.tags or {}), **checked}
            row.tags = {name: value for name, value in merged.items() if value != ""}
            queue.commit()
            return {"id": run_id, "tags": row.tags}
        finally:
            queue.close()
    merged = {**(run.tags or {}), **checked}
    run.tags = {name: value for name, value in merged.items() if value != ""}
    db.commit()
    return {"id": run_id, "tags": run.tags}


def get_run(db: Session, run_id: str) -> Dict[str, Any]:
    """A run: what was run, and its status. A run in the queue has the state queued and its position."""
    try:
        run = _run(db, run_id)
    except RunNotFound:
        row = _queue_row(db, run_id)
        if row is None:
            raise
        return _queued_detail(db, row)
    return {
        "run": run.info,
        "status": {**run.status, "position": None},
        "tags": run.tags or {},
        "can_stop": run.state == "running",
        "has_log": db.query(MillLog).filter(MillLog.run_id == run_id).first() is not None,
    }


def _queued_detail(db: Session, row: MillQueuedRun) -> Dict[str, Any]:
    summary = _queued_summary(row, _positions(db))
    request = row.request
    return {
        "run": {"id": row.id, "task": summary["task"], "file": request["file"], "repository": None,
                "inputs": {name: {"type": "", "path": path} for name, path in request["inputs"].items()},
                "outputs": {name: {"type": "", "path": path} for name, path in request["outputs"].items()},
                "settings": request["settings"], "limit": request["limit"], "runner": RUNNER},
        "status": {key: summary[key] for key in ("state", "position", "error", "started_at", "updated_at",
                                                  "ended_at", "stages", "samples", "failures")}
                  | {"counters": {}, "measures": {}},
        "tags": row.tags or {},
        "can_stop": row.state == "queued",
        "has_log": False,
    }


def _in_queue(db: Session, run_id: str) -> bool:
    return _queue_row(db, run_id) is not None and db.query(MillRun).filter(
        MillRun.id == run_id, MillRun.status_seq > 0).first() is None


def samples(db: Session, run_id: str) -> Dict[str, Any]:
    if _in_queue(db, run_id):
        return {"latest": [], "spaced": []}
    kept = _run(db, run_id).samples
    return kept if kept else {"latest": [], "spaced": []}


def failures(db: Session, run_id: str, limit: int = 50) -> Dict[str, Any]:
    if _in_queue(db, run_id):
        return {"failures": [], "total": 0}
    run = _run(db, run_id)
    first = db.query(MillFailure).filter(MillFailure.run_id == run_id).order_by(MillFailure.n).limit(limit).all()
    return {
        "failures": [{"n": f.n, "at": f.at, "error": f.error, "fields": f.fields} for f in first],
        "total": run.status["failures"],
    }


def log(db: Session, run_id: str, lines: int = 200) -> Dict[str, Any]:
    """The last lines of what a run printed; none for a run in the queue."""
    if _in_queue(db, run_id):
        return {"lines": [], "total_lines": 0}
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
    given_secrets: Optional[Dict[str, str]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a run of a task in this pod at once. Answers once the run's first status is stored.

    A path of an input or an output is relative to the root of the tasks
    repository, or absolute. `token` is the caller's token, which the task
    calls the platform's services with; `tags` are attached to the run;
    `given_secrets` are given to the task as environment variables of their names.
    """
    path = _file_in_repository(file)
    tags = _checked_tags(tags or {})
    run_id = run_id or _new_id(task, path)

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
        command, cwd=repository(), env=_task_env(token, given_secrets), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
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
    _children.pop(run_id, None)
    # Let the output thread take the last lines
    time.sleep(0.2)
    said = "\n".join(child.lines).strip()
    raise RunsError(said or f"the run ended with code {child.process.returncode} before it sent a status")


def _new_id(task: Optional[str], path: Path) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{task or path.stem}-{secrets.token_hex(2)}"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# The queue: a run asked while this pod runs settings.MILL_MAX_RUNS runs waits, and starts in order as earlier runs
# end. Its row (MillQueuedRun) is written through a session of its own, committed at once, so every connection
# sees it as the consumer's rows are seen. _lock orders the decisions to start or to queue.
_lock = threading.Lock()
# The runs being started now, before their process is in _children or while it starts
_reserved: set = set()
# The caller's token and the secrets' values of each run waiting in the queue, by run id: in memory only
_waiting: Dict[str, Dict[str, Any]] = {}
# Why the queue does not move, when it does not: said in the list of runs
dispatch_problem: Optional[str] = None


def _busy() -> int:
    """The runs this pod runs or is starting."""
    live = {run_id for run_id, child in _children.items() if child.process.poll() is None}
    return len(live | _reserved)


def _queue_session() -> Session:
    return get_session_local()()


def _queued(db: Session) -> List[MillQueuedRun]:
    """The runs waiting, first in line first."""
    return (db.query(MillQueuedRun).filter(MillQueuedRun.state == "queued")
            .order_by(MillQueuedRun.queued_at, MillQueuedRun.id).populate_existing().all())


def submit(
    db: Session,
    file: str,
    task: Optional[str],
    inputs: Dict[str, str],
    outputs: Dict[str, str],
    task_settings: Dict[str, Any],
    limit: Optional[int],
    token: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
    given_secrets: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Start a run in this pod when it has room for one and no run waits; queue it otherwise. Answers the run's id
    and state: running (or ended) once its first status is stored, or queued with its position in the queue."""
    path = _file_in_repository(file)
    tags = _checked_tags(tags or {})
    run_id = _new_id(task, path)
    queue = _queue_session()
    try:
        with _lock:
            direct = _busy() < settings.MILL_MAX_RUNS and not _queued(queue)
            if direct:
                _reserved.add(run_id)
            else:
                queue.add(MillQueuedRun(
                    id=run_id, tags=tags, state="queued", queued_at=_now(),
                    request={"file": file, "task": task, "inputs": inputs, "outputs": outputs,
                             "settings": task_settings, "limit": limit, "secrets": sorted(given_secrets or {})},
                ))
                queue.commit()
                _waiting[run_id] = {"token": token, "secrets": given_secrets or {}}
                position = [row.id for row in _queued(queue)].index(run_id) + 1
    finally:
        queue.close()
    if not direct:
        return {"id": run_id, "state": "queued", "position": position}
    try:
        return start(db, file, task, inputs, outputs, task_settings, limit, token, tags, given_secrets, run_id)
    finally:
        with _lock:
            _reserved.discard(run_id)


def dispatch_once() -> bool:
    """Start the first run waiting when this pod has room for it. True when a run left the queue."""
    queue = _queue_session()
    try:
        with _lock:
            if _busy() >= settings.MILL_MAX_RUNS:
                return False
            waiting = _queued(queue)
            if not waiting:
                return False
            row = waiting[0]
            run_id, request, tags = row.id, dict(row.request), dict(row.tags or {})
            given = _waiting.pop(run_id, None)
            _reserved.add(run_id)
        try:
            if given is None:
                _end_queued(queue, run_id, "stopped", RESTARTED)
                return True
            try:
                start(queue, request["file"], request["task"], request["inputs"], request["outputs"],
                      request["settings"], request["limit"], given["token"], tags, given["secrets"], run_id)
            except RunsError as e:
                _end_queued(queue, run_id, "failed", str(e))
                return True
            queue.query(MillQueuedRun).filter(MillQueuedRun.id == run_id).delete()
            queue.commit()
            return True
        finally:
            with _lock:
                _reserved.discard(run_id)
    finally:
        queue.close()


def _end_queued(db: Session, run_id: str, state: str, error: str) -> None:
    row = db.query(MillQueuedRun).filter(MillQueuedRun.id == run_id).populate_existing().one()
    row.state, row.error, row.ended_at = state, error, _now()
    db.commit()


def cancel(run_id: str) -> Dict[str, Any]:
    """Take a run out of the queue before it starts; it ends stopped."""
    queue = _queue_session()
    try:
        with _lock:
            row = queue.query(MillQueuedRun).filter(MillQueuedRun.id == run_id).populate_existing().first()
            if row is None:
                raise RunNotFound(f"there is no run {run_id}")
            if row.state != "queued":
                raise RunsError(f"run {run_id} is {row.state}; only a queued or running run is stopped")
            if run_id not in _waiting:
                raise RunsError(f"run {run_id} is starting now; stop it once it runs")
            del _waiting[run_id]
            _end_queued(queue, run_id, "stopped", "cancelled before it started")
        return {"id": run_id, "state": "stopped"}
    finally:
        queue.close()


def stop_waiting_after_restart() -> List[str]:
    """End the runs a former pod left in the queue: their token and secrets went with it. Run at start."""
    queue = _queue_session()
    try:
        left = _queued(queue)
        for row in left:
            row.state, row.error, row.ended_at = "stopped", RESTARTED, _now()
        queue.commit()
        return [row.id for row in left]
    finally:
        queue.close()


class Dispatcher:
    """Starts queued runs as room frees up, from a thread of its own."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mill-dispatcher")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def _loop(self) -> None:
        global dispatch_problem
        while not self._stop.wait(DISPATCH_SECONDS):
            try:
                while not self._stop.is_set() and dispatch_once():
                    pass
                dispatch_problem = None
            except Exception as e:  # the queue's own failure is said in the list of runs and tried again
                dispatch_problem = f"the queue of runs cannot start its next run: {type(e).__name__}: {e}"


def _iso(at: datetime.datetime) -> str:
    """A time as the runs write theirs (thinkube_mill's now()), so the two sort together."""
    return at.astimezone(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _queued_summary(row: MillQueuedRun, positions: Dict[str, int]) -> Dict[str, Any]:
    request = row.request
    at = _iso(row.queued_at)
    return {
        "id": row.id,
        "task": request["task"] or Path(request["file"]).stem,
        "file": request["file"],
        "limit": request["limit"],
        "host": None,
        "node": None,
        "runner": RUNNER,
        "commit": None,
        "dirty": None,
        "tags": row.tags or {},
        "state": row.state,
        "position": positions.get(row.id),
        "error": row.error,
        "started_at": at,
        "updated_at": _iso(row.ended_at or row.queued_at),
        "ended_at": _iso(row.ended_at) if row.ended_at else None,
        "stages": [],
        "samples": 0,
        "failures": 0,
    }


def _positions(db: Session) -> Dict[str, int]:
    return {row.id: n for n, row in enumerate(_queued(db), start=1)}


def _queue_row(db: Session, run_id: str) -> Optional[MillQueuedRun]:
    return db.query(MillQueuedRun).filter(MillQueuedRun.id == run_id).populate_existing().first()

