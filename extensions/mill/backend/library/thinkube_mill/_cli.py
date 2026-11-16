# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The command line of Mill.

  mill describe <file>
      The tasks a file declares, as JSON.

  mill run <file>[:<task>] --in name=path --out name=path [--set name=value]
           [--limit N] [--run-id ID]
      Run a task. The run reports to the Mill control center through NATS
      (see _report). Prints the run's id when it starts and its last
      status, as JSON, when it ends. Exit code 0 completed, 1 failed,
      143 stopped, 2 the command or the task's declaration is wrong or NATS
      cannot be reached, 4 the run ended and its last record could not be
      sent.
"""

import argparse
import json
import os
import pathlib
import signal
import sys
from typing import Any, Dict, List, Optional

from . import _execute
from ._logtee import LogTee
from ._report import Reporter, ReportError
from ._task import Task, TaskError

EXIT_USAGE = 2


def _pairs(values: Optional[List[str]], flag: str) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    for value in values or []:
        name, sep, rest = value.partition("=")
        if not sep or not name:
            raise TaskError(f"{flag} takes name=value, and was given {value!r}")
        pairs[name] = rest
    return pairs


def _setting_values(task: Task, given: Dict[str, str]) -> Dict[str, Any]:
    """Settings as typed on the command line: text for a text setting, JSON for any other."""
    values: Dict[str, Any] = {}
    for name, text in given.items():
        if isinstance(task.settings.get(name), str):
            values[name] = text
            continue
        try:
            values[name] = json.loads(text)
        except ValueError:
            values[name] = text
    return values


def _split_target(target: str) -> tuple:
    file, sep, name = target.rpartition(":")
    if sep and file and not name.endswith(".py") and "/" not in name:
        return pathlib.Path(file), name
    return pathlib.Path(target), None


def _describe(args: argparse.Namespace) -> int:
    file = pathlib.Path(args.file)
    repository = _execute.repository_of(file)
    root = pathlib.Path(repository["root"]) if repository else None
    tasks = _execute.load_tasks(file, root)
    print(json.dumps([t.describe() for t in tasks.values()], ensure_ascii=False, indent=2))
    return 0


def _run_task(args: argparse.Namespace) -> int:
    file, name = _split_target(args.target)
    repository = _execute.repository_of(file)
    root = pathlib.Path(repository["root"]) if repository else None
    if args.limit is not None and args.limit < 1:
        raise TaskError(f"--limit is the number of items to read, at least 1; it was given {args.limit}")

    # Progress bars drawn by other libraries report to the run too
    from . import tqdm as mill_tqdm

    mill_tqdm.install_if_present()

    task = _execute.pick_task(_execute.load_tasks(file, root), name, file)
    inputs = _pairs(args.inputs, "--in")
    outputs = _pairs(args.outputs, "--out")
    settings = _setting_values(task, _pairs(args.settings, "--set"))
    # What is wrong with the command is said before anything is started
    _execute.check_paths(task, inputs, outputs)
    _execute.resolve_settings(task, settings)

    run_id = args.run_id or _execute.new_run_id(task)
    reporter = Reporter(run_id, on_stop=lambda: os.kill(os.getpid(), signal.SIGTERM))
    print(f"run {run_id}", file=sys.stderr, flush=True)
    log = LogTee(reporter.send)
    try:
        code, run = _execute.execute(file, task, inputs, outputs, settings, args.limit, repository, run_id,
                                     reporter.send)
    finally:
        log.close()
        reporter.close()
    if log.error is not None:
        print(f"mill: the log of the run is not whole: {log.error}", file=sys.stderr)
    print(json.dumps({"id": run_id, **run.status()}, ensure_ascii=False))
    return code


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mill", description="Run the tasks of Thinkube Mill.")
    commands = parser.add_subparsers(dest="command", required=True)

    describe = commands.add_parser("describe", help="the tasks a file declares, as JSON")
    describe.add_argument("file")
    describe.set_defaults(do=_describe)

    run = commands.add_parser("run", help="run a task")
    run.add_argument("target", help="<file> or <file>:<task>")
    run.add_argument("--in", dest="inputs", action="append", metavar="NAME=PATH", help="the path of an input")
    run.add_argument("--out", dest="outputs", action="append", metavar="NAME=PATH", help="the path of an output")
    run.add_argument("--set", dest="settings", action="append", metavar="NAME=VALUE", help="a setting's value")
    run.add_argument("--limit", type=int, help="read only the first N items of each jsonl input")
    run.add_argument("--run-id", help="the run's id; made from the time and the task without it")
    run.set_defaults(do=_run_task)

    args = parser.parse_args(argv)
    try:
        return args.do(args)
    except (TaskError, ReportError) as e:
        print(f"mill: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
