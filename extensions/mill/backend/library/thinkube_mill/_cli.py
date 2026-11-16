# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The command line of Mill.

  mill describe <file>
      The tasks a file declares, as JSON.

  mill run <file>[:<task>] --in name=path --out name=path [--set name=value]
           [--limit N] [--runs-dir DIR] [--run-id ID]
      Run a task. Prints the run's folder when it starts and its last
      status, as JSON, when it ends. Exit code 0 completed, 1 failed,
      143 stopped, 2 the command or the task's declaration is wrong.

  mill status <run folder>
      The status of a run, as JSON; with --samples, its samples too.
"""

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

from . import _execute
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
    records = _execute.runs_dir(args.runs_dir, repository, file)
    if args.limit is not None and args.limit < 1:
        raise TaskError(f"--limit is the number of items to read, at least 1; it was given {args.limit}")

    # Progress bars drawn by other libraries report to the run too
    from . import tqdm as mill_tqdm

    mill_tqdm.install_if_present()

    task = _execute.pick_task(_execute.load_tasks(file, root), name, file)
    inputs = _pairs(args.inputs, "--in")
    outputs = _pairs(args.outputs, "--out")
    settings = _setting_values(task, _pairs(args.settings, "--set"))
    run_id = args.run_id or _execute.new_run_id(task)
    print(f"run {run_id} in {records / run_id}", file=sys.stderr, flush=True)
    code, run = _execute.execute(file, task, inputs, outputs, settings, args.limit, records, repository, run_id)
    print(json.dumps({"id": run_id, "dir": str(run.dir), **run.status()}, ensure_ascii=False))
    return code


def _status(args: argparse.Namespace) -> int:
    run_dir = pathlib.Path(args.run)
    status_file = run_dir / "status.json"
    if not status_file.is_file():
        raise TaskError(f"{run_dir} is not a run's folder: it has no status.json")
    answer = json.loads(status_file.read_text(encoding="utf-8"))
    if args.samples:
        answer["samples_kept"] = json.loads((run_dir / "samples.json").read_text(encoding="utf-8"))
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    return 0


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
    run.add_argument("--runs-dir", help="the folder that keeps the records of runs")
    run.add_argument("--run-id", help="the run's id; made from the time and the task without it")
    run.set_defaults(do=_run_task)

    status = commands.add_parser("status", help="the status of a run, as JSON")
    status.add_argument("run", help="the run's folder")
    status.add_argument("--samples", action="store_true", help="with the samples the run kept")
    status.set_defaults(do=_status)

    args = parser.parse_args(argv)
    try:
        return args.do(args)
    except TaskError as e:
        print(f"mill: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
