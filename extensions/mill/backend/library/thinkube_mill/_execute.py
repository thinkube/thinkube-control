# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Running a task: reading its inputs, calling it, writing its outputs."""

import datetime
import importlib.util
import inspect
import json
import os
import pathlib
import secrets
import signal
import socket
import subprocess
import sys
import traceback
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import _run
from ._report import ReportError
from ._task import OUTPUTS_PARAMETER, SETTINGS_PARAMETER, Task, TaskError

# What a file being written is called until its run completes
PARTIAL = ".partial"

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_STOPPED = 143
# The run ended, and its last record could not be sent
EXIT_UNREPORTED = 4


class Stopped(BaseException):
    """The run was asked to stop."""


class Items:
    """The items of a jsonl input: read one by one, with their number known."""

    def __init__(self, path: pathlib.Path, limit: Optional[int] = None) -> None:
        self.path = path
        with open(path, "rb") as f:
            lines = sum(1 for line in f if line.strip())
        self._total = lines if limit is None else min(lines, limit)

    def __len__(self) -> int:
        return self._total

    def __iter__(self) -> Iterator[Any]:
        given = 0
        with open(self.path, encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                if given >= self._total:
                    return
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except ValueError as e:
                    raise TaskError(f"{self.path}, line {number}: not JSON ({e})") from e
                given += 1
                run = _run.current()
                if run is not None:
                    run.last_input = item
                yield item


class Output:
    """One output of a run. It is written beside its path and takes its name when the run completes."""

    def __init__(self, name: str, kind: str, path: pathlib.Path) -> None:
        self.name = name
        self.kind = kind
        self.final = path
        # A path output is the task's own to write, so it is used as it is
        self.path = path if kind == "path" else path.with_name(path.name + PARTIAL)
        self.written = 0
        self._file = None
        self._set = False
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "jsonl":
            self._file = open(self.path, "w", encoding="utf-8")

    def write(self, item: Any) -> None:
        """Add an item to a jsonl output."""
        if self.kind != "jsonl":
            raise TaskError(f"the output '{self.name}' is {self.kind}: use set(value), not write(item)")
        self._file.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.written += 1

    def set(self, value: Any) -> None:
        """Give a json or text output its value."""
        if self.kind == "json":
            self.path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif self.kind == "text":
            if not isinstance(value, str):
                raise TaskError(f"the output '{self.name}' is text and was given a {type(value).__name__}")
            self.path.write_text(value, encoding="utf-8")
        else:
            raise TaskError(f"the output '{self.name}' is {self.kind}: set(value) is for json and text outputs")
        self._set = True

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def complete(self) -> None:
        """Give the output its name. An output the task never wrote is an error."""
        self.close()
        if self.kind == "path":
            if not self.final.exists():
                raise TaskError(f"the task did not write its output '{self.name}' ({self.final})")
            return
        if self.kind in ("json", "text") and not self._set:
            raise TaskError(f"the task did not set its output '{self.name}'")
        os.replace(self.path, self.final)


def load_tasks(file: pathlib.Path, repository: Optional[pathlib.Path]) -> Dict[str, Task]:
    """The tasks a Python file declares, by name.

    The file's folder and the repository's root are importable while it
    loads, so a task imports the code beside it and the repository's
    shared code.
    """
    if not file.is_file():
        raise TaskError(f"{file} is not a file")
    for folder in (repository, file.parent):
        if folder is not None and str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location(f"mill_task_{file.stem}", file)
    if spec is None or spec.loader is None:
        raise TaskError(f"{file} cannot be loaded as Python")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    tasks = {v.name: v for v in vars(module).values() if isinstance(v, Task)}
    if not tasks:
        raise TaskError(f"{file} declares no task: no function in it has @task")
    return tasks


def pick_task(tasks: Dict[str, Task], name: Optional[str], file: pathlib.Path) -> Task:
    if name is not None:
        if name not in tasks:
            raise TaskError(f"{file} has no task '{name}'. It has: {', '.join(sorted(tasks))}")
        return tasks[name]
    if len(tasks) > 1:
        raise TaskError(
            f"{file} declares several tasks ({', '.join(sorted(tasks))}). Name one: {file}:<task>"
        )
    return next(iter(tasks.values()))


def repository_of(file: pathlib.Path) -> Optional[Dict[str, Any]]:
    """The git repository a file is in: its root, its commit, and whether it has changes not committed."""

    def git(*args: str) -> Optional[str]:
        try:
            done = subprocess.run(
                ["git", "-C", str(file.parent), *args], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    root = git("rev-parse", "--show-toplevel")
    if root is None:
        return None
    changes = git("status", "--porcelain")
    return {"root": root, "commit": git("rev-parse", "HEAD"), "dirty": bool(changes)}


def resolve_settings(task: Task, given: Dict[str, Any]) -> Dict[str, Any]:
    """The task's settings with the given values in place of their defaults."""
    unknown = sorted(set(given) - set(task.settings))
    if unknown:
        known = ", ".join(sorted(task.settings)) or "none"
        raise TaskError(f"task '{task.name}' has no setting {', '.join(unknown)}. Its settings: {known}")
    settings = dict(task.settings)
    for key, value in given.items():
        default = task.settings[key]
        if default is not None and value is not None:
            same = isinstance(value, type(default)) and isinstance(default, bool) == isinstance(value, bool)
            number = isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool)
            if not (same or number):
                raise TaskError(
                    f"task '{task.name}': the setting '{key}' is a {type(default).__name__} "
                    f"(default {default!r}) and was given {value!r}, a {type(value).__name__}"
                )
            if number:
                value = float(value)
        settings[key] = value
    return settings


def check_paths(task: Task, inputs: Dict[str, str], outputs: Dict[str, str]) -> None:
    for kind, declared, given, flag in (("input", task.inputs, inputs, "--in"), ("output", task.outputs, outputs, "--out")):
        unknown = sorted(set(given) - set(declared))
        if unknown:
            raise TaskError(
                f"task '{task.name}' has no {kind} {', '.join(unknown)}. "
                f"Its {kind}s: {', '.join(sorted(declared)) or 'none'}"
            )
        missing = [name for name in declared if name not in given]
        if missing:
            raise TaskError(
                f"task '{task.name}' needs a path for its {kind}(s) {', '.join(missing)}: "
                + " ".join(f"{flag} {name}=<path>" for name in missing)
            )
    for name, path in inputs.items():
        if not pathlib.Path(path).exists():
            raise TaskError(f"the input '{name}' is {path}, which does not exist")


def read_input(kind: str, path: pathlib.Path, limit: Optional[int]) -> Any:
    if kind == "jsonl":
        return Items(path, limit)
    if kind == "json":
        return json.loads(path.read_text(encoding="utf-8"))
    if kind == "text":
        return path.read_text(encoding="utf-8")
    return path


def new_run_id(task: Task) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{task.name}-{secrets.token_hex(2)}"


def _end(run: _run.Run, state: str, error: Optional[str], code: int) -> int:
    """End the run and send its last record. Returns the exit code."""
    try:
        run.finish(state, error)
    except ReportError as e:
        print(f"mill: the run {state}, but its last record could not be sent: {e}", file=sys.stderr)
        return EXIT_UNREPORTED
    return code


def execute(
    file: pathlib.Path,
    task: Task,
    inputs: Dict[str, str],
    outputs: Dict[str, str],
    given_settings: Dict[str, Any],
    limit: Optional[int],
    repository: Optional[Dict[str, Any]],
    run_id: str,
    send: _run.Send,
) -> Tuple[int, _run.Run]:
    """Run a task and report it through ``send``. Returns the exit code and the run."""
    check_paths(task, inputs, outputs)
    settings = resolve_settings(task, given_settings)

    root = pathlib.Path(repository["root"]) if repository else None
    try:
        shown_file = str(file.resolve().relative_to(root.resolve())) if root else str(file)
    except ValueError:
        shown_file = str(file)
    info: Dict[str, Any] = {
        "id": run_id,
        "task": task.name,
        "description": task.description,
        "file": shown_file,
        "repository": repository,
        "inputs": {k: {"type": task.inputs[k], "path": v} for k, v in inputs.items()},
        "outputs": {k: {"type": task.outputs[k], "path": v} for k, v in outputs.items()},
        "settings": settings,
        "limit": limit,
        "host": socket.gethostname(),
        "pid": os.getpid(),
    }
    # What the environment says about where the run executes and what started it
    for key, variable in (("node", "NODE_NAME"), ("runner", "MILL_RUNNER")):
        value = os.environ.get(variable)
        if value:
            info[key] = value

    def stop(signum: int, frame: Any) -> None:
        raise Stopped()

    # In place before the run's first message, so a run that shows as running can always be stopped
    earlier = signal.signal(signal.SIGTERM, stop)
    opened: List[Output] = []
    run: Optional[_run.Run] = None
    try:
        run = _run.Run(info, send)
        _run.set_current(run)
        kwargs: Dict[str, Any] = {}
        parameters = inspect.signature(task.fn).parameters
        for name, kind in task.inputs.items():
            kwargs[name] = read_input(kind, pathlib.Path(inputs[name]), limit)
        out = {name: Output(name, kind, pathlib.Path(outputs[name])) for name, kind in task.outputs.items()}
        opened = list(out.values())
        if SETTINGS_PARAMETER in parameters:
            kwargs[SETTINGS_PARAMETER] = settings
        if OUTPUTS_PARAMETER in parameters:
            kwargs[OUTPUTS_PARAMETER] = out

        result = task.fn(**kwargs)
        if inspect.isgenerator(result):
            target = next(o for o in opened if o.kind == "jsonl")
            for item in result:
                target.write(item)
                run.count(f"written: {target.name}")
                if not run.explicit_samples:
                    run.sample({"input": run.last_input, "output": item})
        elif isinstance(result, dict):
            for name, value in result.items():
                if name not in out:
                    raise TaskError(
                        f"task '{task.name}' returned a value for '{name}', which is not one of its outputs"
                    )
                out[name].set(value)
        elif result is not None:
            raise TaskError(
                f"task '{task.name}' returned a {type(result).__name__}. A task returns nothing, "
                "or a dict with a value for each json or text output."
            )
        for output in opened:
            output.complete()
        return _end(run, "completed", None, EXIT_COMPLETED), run
    except Stopped:
        for output in opened:
            output.close()
        if run is None:
            raise
        return _end(run, "stopped", "the run was asked to stop", EXIT_STOPPED), run
    except Exception as e:
        for output in opened:
            output.close()
        if run is None:
            raise
        traceback.print_exc()
        return _end(run, "failed", f"{type(e).__name__}: {e}", EXIT_FAILED), run
    finally:
        signal.signal(signal.SIGTERM, earlier)
        _run.set_current(None)
