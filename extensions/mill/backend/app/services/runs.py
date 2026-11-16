# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The runs of Mill tasks: starting one in this pod, stopping it, and reading
the record any run keeps.

The tasks repository is a folder of the shared file system, mounted here and
in the IDE. A run's record is a folder of files the thinkube-mill library
writes under .mill/runs of the repository, wherever the run executes: this
pod, the IDE, or a pod on a GPU node. This module only reads those files,
except for a run this pod started and lost, which it marks as failed.
"""

import datetime
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings

# What the library names the runner of the runs this pod starts
RUNNER = "mill"

# Seconds a new run has to write its first record or end
START_WAIT = 15.0

# The longest a description of a file's tasks may take
DESCRIBE_TIMEOUT = 60

# The processes of the runs this pod started, by run id
_children: Dict[str, subprocess.Popen] = {}


class RunsError(Exception):
    """An operation on runs that cannot be done; the message says why."""


class RunNotFound(RunsError):
    """No run has that id."""


def repository() -> Path:
    return Path(settings.MILL_REPOSITORY)


def runs_dir() -> Path:
    return repository() / ".mill" / "runs"


def logs_dir() -> Path:
    return repository() / ".mill" / "logs"


def _as_task_user() -> Dict[str, Any]:
    """What makes a child process run as the owner of the tasks repository, when this process is another user."""
    if os.geteuid() == settings.MILL_TASK_UID:
        return {}
    return {"user": settings.MILL_TASK_UID, "group": settings.MILL_TASK_GID}


def _own(path: Path) -> None:
    """Give a file or folder this process made to the owner of the tasks repository."""
    if os.geteuid() != settings.MILL_TASK_UID:
        os.chown(path, settings.MILL_TASK_UID, settings.MILL_TASK_GID)


def _task_env() -> Dict[str, str]:
    home = Path(settings.MILL_TASK_HOME)
    home.mkdir(parents=True, exist_ok=True)
    _own(home)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "PYTHONUNBUFFERED": "1",
        "MILL_RUNNER": RUNNER,
    }
    node = os.environ.get("NODE_NAME")
    if node:
        env["NODE_NAME"] = node
    return env


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_dir(run_id: str) -> Path:
    if not run_id or "/" in run_id or run_id.startswith("."):
        raise RunNotFound(f"'{run_id}' is not a run id")
    run_dir = runs_dir() / run_id
    if not (run_dir / "status.json").is_file():
        raise RunNotFound(f"there is no run {run_id}")
    return run_dir


def _file_in_repository(file: str) -> Path:
    root = repository().resolve()
    path = (root / file).resolve()
    if root != path and root not in path.parents:
        raise RunsError(f"{file} is outside the tasks repository")
    if not path.is_file():
        raise RunsError(f"the tasks repository has no file {file}")
    return path


def _summary(run_dir: Path) -> Dict[str, Any]:
    info = _read(run_dir / "run.json")
    status = _read(run_dir / "status.json")
    repo = info.get("repository") or {}
    return {
        "id": info["id"],
        "task": info["task"],
        "file": info["file"],
        "limit": info.get("limit"),
        "host": info.get("host"),
        "node": info.get("node"),
        "runner": info.get("runner"),
        "commit": repo.get("commit"),
        "dirty": repo.get("dirty"),
        "state": status["state"],
        "error": status["error"],
        "started_at": status["started_at"],
        "updated_at": status["updated_at"],
        "ended_at": status["ended_at"],
        "stages": status["stages"],
        "samples": status["samples"],
        "failures": status["failures"],
    }


def _alive_here(run_id: str) -> bool:
    child = _children.get(run_id)
    return child is not None and child.poll() is None


def reconcile() -> List[str]:
    """Mark as failed the running runs this pod's runner started and that no process runs any more.

    Such a run was cut short: its pod stopped, or its container started
    again. Its record still says "running", and nothing will change it.
    Returns the ids marked.
    """
    for run_id in [i for i, child in _children.items() if child.poll() is not None]:
        del _children[run_id]
    marked = []
    folder = runs_dir()
    if not folder.is_dir():
        return marked
    here = socket.gethostname()
    for run_dir in folder.iterdir():
        status_file = run_dir / "status.json"
        if not status_file.is_file():
            continue
        status = _read(status_file)
        if status["state"] != "running":
            continue
        info = _read(run_dir / "run.json")
        if info.get("runner") != RUNNER:
            continue
        if info.get("host") == here and _alive_here(info["id"]):
            continue
        ended = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
        status.update(
            state="failed",
            error=f"the process of this run ended without finishing it: its pod ({info.get('host')}) stopped or started again",
            ended_at=ended,
            updated_at=ended,
        )
        for stage in status["stages"]:
            if stage["ended_at"] is None:
                stage["ended_at"] = ended
        beside = status_file.with_name("status.json.tmp")
        beside.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        _own(beside)
        os.replace(beside, status_file)
        marked.append(info["id"])
    return marked


def list_runs(limit: int = 50) -> Dict[str, Any]:
    """The newest runs, newest first."""
    reconcile()
    folder = runs_dir()
    if not folder.is_dir():
        return {"runs": [], "total": 0}
    started = []
    for run_dir in folder.iterdir():
        # A folder whose first record is not written yet is a run that is still starting
        if (run_dir / "status.json").is_file():
            started.append(((run_dir / "run.json").stat().st_mtime, run_dir))
    started.sort(key=lambda pair: pair[0], reverse=True)
    return {"runs": [_summary(run_dir) for _, run_dir in started[:limit]], "total": len(started)}


def get_run(run_id: str) -> Dict[str, Any]:
    """A run: what was run, and its status."""
    run_dir = _run_dir(run_id)
    info = _read(run_dir / "run.json")
    return {
        "run": info,
        "status": _read(run_dir / "status.json"),
        "can_stop": info.get("host") == socket.gethostname() and _alive_here(run_id),
        "has_log": (logs_dir() / f"{run_id}.log").is_file(),
    }


def samples(run_id: str) -> Dict[str, Any]:
    kept = _read(_run_dir(run_id) / "samples.json")
    return {"latest": kept["latest"], "spaced": kept["spaced"]}


def failures(run_id: str, limit: int = 50) -> Dict[str, Any]:
    run_dir = _run_dir(run_id)
    total = _read(run_dir / "status.json")["failures"]
    file = run_dir / "failures.jsonl"
    items = []
    if file.is_file():
        with open(file, encoding="utf-8") as f:
            for line in f:
                if len(items) >= limit:
                    break
                items.append(json.loads(line))
    return {"failures": items, "total": total}


def log(run_id: str, lines: int = 200) -> Dict[str, Any]:
    """The last lines of what a run printed. Only a run this pod started has a log here."""
    _run_dir(run_id)
    file = logs_dir() / f"{run_id}.log"
    if not file.is_file():
        raise RunsError(
            f"run {run_id} has no log here: the control center keeps the log of the runs it starts, "
            "and this one was started elsewhere"
        )
    text = file.read_text(encoding="utf-8", errors="replace").splitlines()
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
    file: str,
    task: Optional[str],
    inputs: Dict[str, str],
    outputs: Dict[str, str],
    task_settings: Dict[str, Any],
    limit: Optional[int],
) -> Dict[str, Any]:
    """Start a run of a task in this pod. Answers once the run has its first record.

    A path of an input or an output is relative to the root of the tasks
    repository, or absolute.
    """
    path = _file_in_repository(file)
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
    command += ["--runs-dir", str(runs_dir()), "--run-id", run_id]

    for folder in (runs_dir().parent, runs_dir(), logs_dir()):
        if not folder.is_dir():
            folder.mkdir(parents=True)
            _own(folder)
    log_file = logs_dir() / f"{run_id}.log"
    with open(log_file, "w", encoding="utf-8") as out:
        _own(log_file)
        child = subprocess.Popen(
            command, cwd=repository(), env=_task_env(), stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True, **_as_task_user(),
        )
    _children[run_id] = child

    status_file = runs_dir() / run_id / "status.json"
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        if status_file.is_file():
            return {"id": run_id, "state": _read(status_file)["state"]}
        if child.poll() is not None:
            break
        time.sleep(0.1)
    if status_file.is_file():
        return {"id": run_id, "state": _read(status_file)["state"]}
    if child.poll() is None:
        raise RunsError(
            f"run {run_id} did not write its first record within {int(START_WAIT)} seconds; "
            f"it is still starting. Its log is {log_file}"
        )
    del _children[run_id]
    said = log_file.read_text(encoding="utf-8", errors="replace").strip()
    raise RunsError(said or f"the run ended with code {child.returncode} before it wrote a record")


def stop(run_id: str) -> Dict[str, Any]:
    """Ask a run this pod started to stop. The task ends at once, as stopped."""
    run_dir = _run_dir(run_id)
    info = _read(run_dir / "run.json")
    state = _read(run_dir / "status.json")["state"]
    if state != "running":
        raise RunsError(f"run {run_id} is {state}; only a running run is stopped")
    if info.get("host") != socket.gethostname() or not _alive_here(run_id):
        raise RunsError(
            f"run {run_id} runs on {info.get('host')}, not in the control center's pod, and is stopped there"
        )
    os.killpg(_children[run_id].pid, signal.SIGTERM)
    return {"id": run_id, "asked": "stop"}
