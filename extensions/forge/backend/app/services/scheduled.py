# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Forge's jobs, given to the Scheduler.

A run's preprocessing, revision, plan and training, and a registered model's exports, are each a job of the
Scheduler's: a process in a pod on the node, on the GPUs the Scheduler gives it, which runs one of Forge's scripts
(app/training/*.py) in the Python environment the job names, with its parameters in FORGE_PARAMETERS. The
Scheduler decides when it runs, among all the work on the node. A job of a higher priority may stop it; a training
is resumable, and goes on from its last checkpoint when its turn comes again.

A job's request is the one runs.py builds (job_request, export_request, nvfp4_request): the script as a notebook
of cells, its parameters, its Python environment (kernel_name), its node, GPUs, memory and CPU cores, and its
name, priority and whether it is resumable. Its status is given in the words Forge uses for a job: starting
(waiting for its turn, or for its pod; `waiting` says which), running, completed, error (`error`: the exception
its process raised, from its last lines), or cancelled (stopped).
"""

import json
import re
from typing import Any, Dict, List

import httpx

from app.core.config import settings
from app.services.control import ControlError

# The Scheduler's routes for the parts of the platform, at its Service in Control's namespace
SCHEDULER_URL = "http://scheduler-backend:8000/internal/scheduler"
OWNER = "forge"
IMAGE = "library/tk-jupyter-base:latest"
# The Python environments on each node: the platform's own (fine-tuning) at <arch>/<name>, and those built in
# Thinkube Control (meepsquared, nvfp4) at custom/<name>/<arch>
VENVS = "/var/lib/jupyterhub-venvs"
SYSTEM_VENVS = ("fine-tuning", "agent-dev")
# The models folder (the run's files and the preprocessed data), MLflow's files (the datasets, the stop file, and
# the staging of a registered model), and the Python environments
MOUNTS = ["models", "mlflow", "venvs"]
HOME = "/home/thinkube"
# Where thinkube_models, the platform's helpers for MLflow and the mirrored models, is in the image
HELPERS = "/opt/thinkube"
# The seconds a job has to end once it is stopped
STOP_SECONDS = 120
# The variables of Forge's backend a job's script reads, to reach MLflow
MLFLOW_ENV = ("MLFLOW_KEYCLOAK_TOKEN_URL", "MLFLOW_KEYCLOAK_CLIENT_ID", "MLFLOW_CLIENT_SECRET",
              "MLFLOW_AUTH_USERNAME", "MLFLOW_AUTH_PASSWORD")
# The Scheduler's states, in Forge's words for a job
STATUS = {"waiting": "starting", "running": "running", "stopping": "running", "completed": "completed",
          "failed": "error", "stopped": "cancelled"}
# A line that names an exception: "ValueError: ..." or "meepsquared.job.MeasureAgain: ..."
EXCEPTION_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Again|Refused)\b.*")


def script(notebook: Dict[str, Any]) -> str:
    """The script of a job: its parameters read from FORGE_PARAMETERS, then the notebook's cells in order."""
    cells = [cell["source"] for cell in notebook["cells"]]
    return "\n".join(["import json, os", "PARAMETERS = json.loads(os.environ['FORGE_PARAMETERS'])", *cells])


def command(kernel: str) -> List[str]:
    """The Python of the job's environment on the node's architecture, running FORGE_SCRIPT."""
    arch = '"$(uname -m | sed -e s/x86_64/amd64/ -e s/aarch64/arm64/)"'
    folder = f"{VENVS}/{arch}/{kernel}" if kernel in SYSTEM_VENVS else f"{VENVS}/custom/{kernel}/{arch}"
    return ["sh", "-c", f'exec {folder}/bin/python3 -u -c "$FORGE_SCRIPT"']


def environment(token: str) -> Dict[str, str]:
    """What every job's script reads: MLflow's address and credentials, the cluster's domain, Control's address,
    and the API token of whoever started it."""
    import os

    missing = [name for name in MLFLOW_ENV + ("DOMAIN_NAME",) if not os.environ.get(name)]
    if missing:
        raise ControlError(500, f"Forge's backend has no {', '.join(missing)}, which its jobs need to reach MLflow; "
                                "deploy Forge again")
    found = {name: os.environ[name] for name in MLFLOW_ENV}
    domain = os.environ["DOMAIN_NAME"]
    return {**found, "MLFLOW_TRACKING_URI": settings.MLFLOW_URL, "DOMAIN_NAME": domain,
            "THINKUBE_CONTROL_URL": f"https://control.{domain}", "THINKUBE_API_TOKEN": token, "HOME": HOME,
            "PYTHONPATH": HELPERS}


def job_spec(request: Dict[str, Any], token: str) -> Dict[str, Any]:
    """The Scheduler's job for a request of runs.py."""
    return {
        "name": request["name"],
        "owner": OWNER,
        "node": request["node"],
        "image": IMAGE,
        "command": command(request["kernel_name"]),
        "env": {**environment(token), "FORGE_SCRIPT": script(request["notebook"]),
                "FORGE_PARAMETERS": json.dumps(request["parameters"], default=str)},
        "working_dir": HOME,
        "mounts": MOUNTS,
        "gpus": request.get("gpus") or 0,
        # A plan or a training has its GPUs alone
        "gpu_memory_gb": None,
        "memory_gb": request["memory_gb"],
        "cpu_cores": request.get("cpu_cores") or 4,
        "priority": request["priority"],
        "resumable": request.get("resumable", False),
        "stop_seconds": STOP_SECONDS,
    }


def raised(error: str) -> str:
    """The exception a job's process raised, from its last lines; the whole error when no line names one."""
    lines = [line.strip() for line in (error or "").splitlines()]
    named = [line for line in lines if EXCEPTION_LINE.match(line)]
    return named[-1] if named else (error or "no error given")


def view(job: Dict[str, Any]) -> Dict[str, Any]:
    """A job of the Scheduler in Forge's words for a job."""
    status = STATUS[job["status"]]
    error = job.get("error")
    return {"job_id": job["id"], "status": status, "node": job["node"], "gpus": job["gpus"],
            "waiting": job.get("waiting"), "error": raised(error) if status == "error" else error,
            "starts": job.get("starts"), "preempted_by": job.get("preempted_by"), "results": None}


async def _call(method: str, path: str, body: Dict[str, Any] = None) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        try:
            r = await client.request(method, SCHEDULER_URL + path, json=body)
        except httpx.HTTPError as e:
            raise ControlError(502, f"the Scheduler did not answer {method} {path}: {e}")
    if r.status_code >= 400:
        raise ControlError(r.status_code, f"the Scheduler answered {method} {path} with {r.status_code}: "
                                          f"{r.text[:500]}")
    return r.json()


async def start(request: Dict[str, Any], token: str) -> Dict[str, Any]:
    return view(await _call("POST", "/jobs", job_spec(request, token)))


async def get(job_id: str) -> Dict[str, Any]:
    return view(await _call("GET", f"/jobs/{job_id}"))


async def stop(job_id: str) -> Dict[str, Any]:
    """Stop a job: a waiting one at once, a running one gets SIGTERM and STOP_SECONDS. One that has ended is left
    as it is."""
    found = await _call("GET", f"/jobs/{job_id}")
    if found["status"] in ("waiting", "running", "stopping"):
        found = await _call("POST", f"/jobs/{job_id}/stop")
    return view(found)


async def log(job_id: str, lines: int = 200) -> Dict[str, Any]:
    return await _call("GET", f"/jobs/{job_id}/log?lines={lines}")
