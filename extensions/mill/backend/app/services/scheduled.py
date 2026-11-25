# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A task run on a chosen node: a job Mill gives the Scheduler.

The Scheduler decides when the job runs, among all the work on its node, with the priority of Mill tasks (KIND)
unless the request gives one, and starts its pod: on the node, with the GPUs the Scheduler gives it, from the AI
base image (or the notebook servers' image, for a task that runs in one of their Python environments), with the
shared file system mounted where the main pod has it, so a path of the tasks repository means the same in both.
The pod installs thinkube-mill and runs `mill run`, which reports to the control center through NATS like any run.

Each start of the job is a run of Mill of its own, named after the job and the start (run_id): a job stopped for
work of a higher priority starts again from the beginning when its turn comes. A task that goes on where it stopped
keeps its state in MILL_STATE_DIR, a folder of the job on the shared file system, the same at every start, and
reads MILL_ATTEMPT (1 at the first start). The folder is removed when the task ends well, and kept when it is
stopped or fails. With models, MILL_MODELS is a JSON object of each mirrored model's folder by its id, on MLflow's
file system, resolved when the job is given: every start reads the same version.
"""

import json
import shlex
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

SCHEDULER_URL = "http://scheduler-backend.thinkube-control.svc.cluster.local:8000/internal/scheduler"
OWNER = "mill"
# The kind of work a Mill task is, whose priority a person sets on the Scheduler's Priorities page
KIND = "mill_task"
TASK_IMAGE = "library/ai-inference-base:cuda13.0-torch2.9-py3.12"
# A task in a Python environment of the notebook servers runs on their image
VENV_IMAGE = "library/tk-jupyter-base:latest"
# Where the Scheduler mounts the shared file system, MLflow's files and the nodes' Python environments
SHARED_MOUNT = "/storage/thinkube-shared"
MLFLOW_MOUNT = "/home/thinkube/thinkube/mlflow"
VENVS_MOUNT = "/var/lib/jupyterhub-venvs"
# The folder of the shared file system that holds each job's state folder
STATE_FOLDER = "mill-jobs"
# The environment variables Mill gives every task, which a secret's name may not take
GIVEN_ENV = ["HOME", "PIP_INDEX_URL", "PYTHONUNBUFFERED", "MILL_RUNNER", "MILL_NATS_URL", "MILL_SUBJECTS",
             "LLM_GATEWAY_URL", "MILL_JOB_ID", "MILL_RUN_PREFIX", "MILL_STATE_DIR", "NODE_NAME", "THINKUBE_API_TOKEN",
             "MILL_MODELS", "MILL_ATTEMPT", "RUN_ID", "PYTHONPATH", "PYTHON", "ARCH"]
# Seconds a task has to end after it is stopped
STOP_SECONDS = 60
# Seconds a task's pod waits for NATS before its task starts
NATS_WAIT_SECONDS = 300
# The wait, run by the image's Python before the task: a TCP connection to MILL_NATS_URL, tried every 2 seconds
NATS_WAIT = f"""\
import os, socket, sys, time
from urllib.parse import urlparse
url = urlparse(os.environ["MILL_NATS_URL"])
end = time.monotonic() + {NATS_WAIT_SECONDS}
while True:
    try:
        socket.create_connection((url.hostname, url.port or 4222), timeout=5).close()
        break
    except OSError as e:
        if time.monotonic() > end:
            sys.exit(f"NATS at {{url.geturl()}} did not answer within {NATS_WAIT_SECONDS} seconds: {{e}}")
        time.sleep(2)
"""


class SchedulerError(Exception):
    """The Scheduler refused or did not answer; `status` is its HTTP status, the message says why."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def run_prefix(job_id: str) -> str:
    """The start of the ids of a job's runs in Mill: mill-<the job's first 8 characters>."""
    return f"mill-{job_id[:8]}"


def state_folder(job_id: str) -> str:
    return f"{SHARED_MOUNT}/{STATE_FOLDER}/{job_id}/state"


def command(task: Dict[str, Any]) -> List[str]:
    """`mill run` of the task, as the pod runs it; the run's id is given by the script."""
    target = f"{task['file']}:{task['task']}" if task.get("task") else task["file"]
    args = ["-m", "thinkube_mill", "run", target]
    for name, path in task.get("inputs", {}).items():
        args += ["--in", f"{name}={path}"]
    for name, path in task.get("outputs", {}).items():
        args += ["--out", f"{name}={path}"]
    for name, value in task.get("settings", {}).items():
        args += ["--set", f"{name}={value if isinstance(value, str) else json.dumps(value)}"]
    if task.get("limit") is not None:
        args += ["--limit", str(task["limit"])]
    return args


def script(task: Dict[str, Any]) -> str:
    """What the pod's shell runs: thinkube-mill installed, then the task, as the run of this start.

    The shell stays up while the task runs, to remove the state folder when the task ends well, and passes SIGTERM
    on to the task, so a task that is stopped can save its state first. Without a venv, Mill is installed for the
    image's Python. With one, the venv of the node's architecture runs the task, and Mill is installed without its
    dependencies into a folder of its own, put first on the path, so that it cannot replace a package of the venv.
    Before the task starts, the shell waits for NATS, and the start fails, saying so, when NATS does not answer."""
    venv = task.get("venv")
    lines = ["set -e", 'mkdir -p "$MILL_STATE_DIR"',
             # Each start of the job is a run of its own in Mill
             'RUN_ID="$MILL_RUN_PREFIX-$SCHEDULER_START"', 'export MILL_ATTEMPT="$SCHEDULER_START"']
    if venv:
        python = f"{VENVS_MOUNT}/custom/{venv}/$ARCH/bin/python"
        lines += [
            'case "$(uname -m)" in aarch64) ARCH=arm64 ;; *) ARCH=amd64 ;; esac',
            f'PYTHON="{python}"',
            f'[ -x "$PYTHON" ] || {{ echo "the venv {venv} is not on $NODE_NAME for $ARCH ($PYTHON): '
            f'build it in Thinkube Control for this architecture" >&2; exit 1; }}',
            '"$PYTHON" -m pip install -q --no-deps --target /tmp/thinkube-mill thinkube-mill',
            "export PYTHONPATH=/tmp/thinkube-mill",
        ]
    else:
        lines += ["pip install -q --user --break-system-packages --no-warn-script-location thinkube-mill",
                  'PYTHON="python3"']
    task_command = ['"$PYTHON"'] + [shlex.quote(a) for a in command(task)] + ["--run-id", '"$RUN_ID"']
    lines += [
        f"python3 -c {shlex.quote(NATS_WAIT)}",
        "set +e",
        f"{' '.join(task_command)} &",
        "child=$!",
        "trap 'kill -TERM \"$child\" 2>/dev/null' TERM",
        'wait "$child"',
        "status=$?",
        # A wait broken by SIGTERM returns early; the task is waited for until it ends
        'while kill -0 "$child" 2>/dev/null; do wait "$child"; status=$?; done',
        'if [ "$status" -eq 0 ]; then rm -rf "$MILL_STATE_DIR"; fi',
        'exit "$status"',
    ]
    return "\n".join(lines) + "\n"


def job_spec(job_id: str, request: Dict[str, Any], models: Dict[str, str], priority: int,
             token: Optional[str], secrets: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The Scheduler's job of a task run. The task is given the LLM Gateway's address and, as THINKUBE_API_TOKEN,
    the token of whoever gave it, so a task that calls a model can, and the secrets it asked for, by their names.
    The Scheduler keeps them with the job, so every start of the job has them."""
    task = request["task"]
    env = {"HOME": "/tmp/home", "PIP_INDEX_URL": f"https://packages-api.{settings.DOMAIN_NAME}/root/stable/+simple/",
           "PYTHONUNBUFFERED": "1", "MILL_RUNNER": "scheduler", "MILL_NATS_URL": settings.MILL_NATS_URL,
           "MILL_SUBJECTS": settings.MILL_SUBJECTS, "LLM_GATEWAY_URL": settings.MILL_LLM_GATEWAY_URL,
           "MILL_JOB_ID": job_id, "MILL_RUN_PREFIX": run_prefix(job_id), "MILL_STATE_DIR": state_folder(job_id),
           "NODE_NAME": request["node"]}
    if token:
        env["THINKUBE_API_TOKEN"] = token
    env.update(secrets or {})
    mounts = ["shared"]
    if task.get("venv"):
        mounts.append("venvs")
    if models:
        env["MILL_MODELS"] = json.dumps({model: f"{MLFLOW_MOUNT}/{folder}" for model, folder in models.items()})
        mounts.append("mlflow")
    return {
        "name": request["name"], "owner": OWNER, "node": request["node"],
        "image": VENV_IMAGE if task.get("venv") else TASK_IMAGE,
        "command": ["sh", "-c", script(task)], "env": env, "working_dir": settings.MILL_REPOSITORY,
        "mounts": mounts, "gpus": request["gpus"], "gpu_memory_gb": request.get("gpu_memory_gb"),
        "memory_gb": request["memory_gb"], "cpu_cores": request["cpu_cores"], "priority": priority,
        # A task starts over from the beginning; one that keeps state reads its state folder
        "resumable": False, "stop_seconds": STOP_SECONDS,
    }


async def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        try:
            r = await client.request(method, SCHEDULER_URL + path, json=body)
        except httpx.HTTPError as e:
            raise SchedulerError(502, f"the Scheduler did not answer {method} {path}: {e}")
    if r.status_code >= 400:
        raise SchedulerError(r.status_code, f"the Scheduler answered {method} {path} with {r.status_code}: "
                                            f"{r.text[:500]}")
    return r.json()


async def priority() -> int:
    """The priority of Mill tasks, as a person set it on the Scheduler's Priorities page."""
    return (await _call("GET", "/priorities"))[KIND]


async def nodes() -> List[str]:
    return [n["name"] for n in (await _call("GET", "/nodes"))["nodes"]]


async def give(spec: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("POST", "/jobs", spec)


async def get(job_id: str) -> Dict[str, Any]:
    return await _call("GET", f"/jobs/{job_id}")


async def stop(job_id: str) -> Dict[str, Any]:
    found = await get(job_id)
    if found["status"] in ("waiting", "running", "stopping"):
        found = await _call("POST", f"/jobs/{job_id}/stop")
    return found


async def log(job_id: str, lines: int) -> Dict[str, Any]:
    return await _call("GET", f"/jobs/{job_id}/log?lines={lines}")
