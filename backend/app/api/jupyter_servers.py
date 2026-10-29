# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Notebook servers, one per node, and unattended notebook runs.

A notebook server is a pod that JupyterHub starts on one node with a CPU,
memory and GPU budget. Each node runs at most one interactive server, a
JupyterHub named server called after the node, which starts with the node's
defaults unless other values are asked for. These endpoints let Claude Code
and Thinkube IDE see every node's server, start one where the work needs to
be, and stop it when the GPU should go back to serving models.

Starting and stopping take from seconds to minutes (an image pull, a pod's
start), so both answer as soon as the Hub has the request, and the caller
follows the server's state in jupyter_notebook_status. A start the Hub gives
up on is watched here, and its reason is reported with the node's state.

An unattended run gets its own named server: started with the resources the
run needs, told to run the notebook top to bottom, and stopped when the run
ends, so nothing is left holding a GPU. Progress is polled by thinkube-control
and recorded in the ``notebook_jobs`` table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import jupyter_notebooks
from app.api.cluster_resources import get_cluster_resources
from app.api.jupyterhub_config import defaults_for_nodes
from app.core.api_tokens import get_current_user_dual_auth
from app.core.config import settings
from app.db.session import SessionLocal, get_db
from app.models.jupyter_venvs import JupyterVenv
from app.models.notebook_jobs import NotebookJob
from app.services import jupyterhub_client as hub
from app.services import storage
from app.services import training_mode
from app.services import notebook_resources as res

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyter", tags=["jupyter-server"])

PROFILE = "thinkube-notebooks"
JOB_POLL_SECONDS = 15
JOB_SERVER_PREFIX = jupyter_notebooks.JOB_SERVER_PREFIX

_job_tasks: Dict[str, asyncio.Task] = {}
# Why the last start of a node's server failed, while it has not been started again.
_start_failures: Dict[str, str] = {}
_start_watchers: Dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

class Placement(BaseModel):
    node: str
    cpu_cores: int
    memory_gb: int
    gpus: int
    # What the spawner requests as nvidia.com/gpu: on a node that shares its
    # GPUs in time slices, every GPU means every slice (see notebook_resources)
    gpu_slots: int = 0
    # The spawner requests a quarter of memory_gb, so a server can be placed
    # where memory is short and still grow to its limit. A run that must not
    # be killed for memory asks the node to reserve all of it.
    reserve_memory: bool = False
    # Training mode keeps new pods off its node; a job for that node is the
    # training's own and is let through.
    training_node: bool = False

    def user_options(self) -> Dict[str, str]:
        return {
            "profile": PROFILE,
            "reserve_memory": "yes" if self.reserve_memory else "no",
            "training_node": "yes" if self.training_node else "no",
            "node": self.node,
            "cpu": str(self.cpu_cores),
            "memory": f"{self.memory_gb}G",
            "enable_gpu": str(self.gpu_slots),
            "gpus": str(self.gpus),
        }


async def _nodes() -> Dict[str, Dict[str, Any]]:
    nodes = {n["name"]: n for n in await get_cluster_resources()}
    if not nodes:
        raise HTTPException(status_code=503, detail="The cluster's node resources are not known yet; try again in a moment.")
    return nodes


async def _check_kernel_fits_node(db: Session, kernel_name: str, node: str) -> None:
    """A venv that needs a GPU is kept only on the GPU nodes, so its kernel exists nowhere else."""
    venv = db.query(JupyterVenv).filter_by(name=kernel_name).first()
    if venv is None or not venv.needs_gpu:
        return
    if not res.node_capacity((await _nodes())[node])["gpus"]:
        raise HTTPException(
            status_code=422,
            detail=f"the kernel {kernel_name} is a venv that needs a GPU, and {node} has no Thinkube GPU, so the venv "
                   "is not there; run the job on a GPU node",
        )


async def resolve_placement(
    db: Session,
    node: Optional[str],
    cpu_cores: Optional[int],
    memory_gb: Optional[int],
    gpus: Optional[int],
) -> Placement:
    """Fill what was not said from the node's defaults and check it against the node."""
    nodes = await _nodes()
    if not node:
        raise HTTPException(status_code=422, detail=f"node is required; the nodes are {sorted(nodes)}")
    if node not in nodes:
        raise HTTPException(status_code=422, detail=f"node '{node}' is not in the cluster; the nodes are {sorted(nodes)}")
    info = nodes[node]
    defaults = next(d for d in defaults_for_nodes(db, [info]) if d.node == node)

    placement = Placement(
        node=node,
        cpu_cores=cpu_cores if cpu_cores is not None else defaults.cpu_cores,
        memory_gb=memory_gb if memory_gb is not None else defaults.memory_gb,
        gpus=gpus if gpus is not None else defaults.gpus,
    )
    capacity = res.node_capacity(info)
    choices = res.node_choices(info)
    free_gpus = int(info["available"].get("gpu", 0))
    problems = []
    if placement.cpu_cores not in choices["cpu_cores"]:
        problems.append(f"cpu_cores must be one of {choices['cpu_cores']} on {node}")
    if placement.memory_gb not in choices["memory_gb"]:
        problems.append(f"memory_gb must be one of {choices['memory_gb']} on {node}")
    if placement.gpus not in choices["gpus"]:
        if not capacity["gpus"]:
            problems.append(f"{node} has no GPU")
        elif capacity["gpu_slices"] > 1:
            problems.append(f"gpus must be one of {choices['gpus']} on {node}: it shares its GPUs in time slices, "
                            "so a server takes one slice or every GPU")
        else:
            problems.append(f"gpus must be one of {choices['gpus']} on {node}")
    else:
        placement.gpu_slots = res.gpu_slots(capacity, placement.gpus)
        if placement.gpu_slots > free_gpus:
            problems.append(
                f"{node} has {free_gpus} GPU slots free of {int(info['capacity'].get('gpu', 0))}; "
                f"{placement.gpus} GPUs need {placement.gpu_slots}; a served model may be holding the rest "
                "(unload_llm_model frees one)"
            )
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    return placement


def _placement_of(model: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    options = (model or {}).get("user_options") or {}
    memory = str(options.get("memory", "")).rstrip("G")
    return {
        "node": options.get("node"),
        "cpu_cores": int(options["cpu"]) if str(options.get("cpu", "")).isdigit() else None,
        "memory_gb": int(memory) if memory.isdigit() else None,
        # gpus says how many GPUs; enable_gpu is the slot count the spawner got, and all a server started
        # before gpus was recorded has
        "gpus": int(options["gpus"]) if str(options.get("gpus", "")).isdigit()
        else int(options["enable_gpu"]) if str(options.get("enable_gpu", "")).isdigit() else None,
    }


def _state(model: Optional[Dict[str, Any]]) -> str:
    if not model:
        return "stopped"
    if model.get("ready"):
        return "running"
    if model.get("pending") == "spawn":
        return "starting"
    if model.get("pending") == "stop":
        return "stopping"
    return "stopped"


# ---------------------------------------------------------------------------
# Servers: status, start, stop
# ---------------------------------------------------------------------------

class NodeServer(BaseModel):
    node: str
    server_name: str = Field(..., description="The JupyterHub named server on this node; the same as the node.")
    state: str = Field(..., description="running, starting, stopping or stopped.")
    cpu_cores: Optional[int] = Field(None, description="What the server was started with; the node's default when stopped.")
    memory_gb: Optional[int] = None
    gpus: Optional[int] = None
    defaults: Dict[str, int] = Field(..., description="The node's default cpu_cores, memory_gb and gpus.")
    capacity: Dict[str, int] = Field(..., description="The most a server on this node can be given.")
    gpus_free: int = Field(0, description="GPU slots on the node not taken now.")
    url: Optional[str] = None
    last_activity: Optional[str] = None
    extension: Optional[Dict[str, Any]] = Field(None, description="tk-notebook-mcp health inside the server, when running.")
    kernels: Optional[List[Dict[str, Any]]] = Field(None, description="The kernels running on the server, with their notebooks, when running.")
    error: Optional[str] = Field(None, description="Why the server's kernels could not be read.")
    start_error: Optional[str] = Field(None, description="Why the last start of this server failed, when it did.")


class OtherServer(BaseModel):
    server_name: str = Field(..., description="The unattended run's server.")
    kind: str = Field(..., description="'unattended-run'.")
    state: str
    node: Optional[str] = None
    cpu_cores: Optional[int] = None
    memory_gb: Optional[int] = None
    gpus: Optional[int] = None
    url: Optional[str] = None


class ServersStatus(BaseModel):
    servers: List[NodeServer] = Field(..., description="One entry per node.")
    other_servers: List[OtherServer] = Field(default_factory=list, description="The servers of unattended runs.")
    message: str


def _url(model: Optional[Dict[str, Any]]) -> Optional[str]:
    return f"https://notebooks.{settings.DOMAIN_NAME}{model['url']}" if model and model.get("url") else None


async def _running_details(server_name: str) -> Dict[str, Any]:
    details: Dict[str, Any] = {}
    try:
        details["extension"] = await jupyter_notebooks.extension_health(server_name)
    except HTTPException as e:
        details["extension"] = {"status": "unreachable", "error": e.detail}
    if details["extension"].get("status") == "ok":
        try:
            kernels = await jupyter_notebooks.call_tool("list_kernels", {}, timeout=10.0, server_name=server_name)
            details["kernels"] = kernels.get("running", []) if isinstance(kernels, dict) else []
        except HTTPException as e:
            details["error"] = str(e.detail)
    return details


async def _servers_status(db: Session) -> ServersStatus:
    nodes = await _nodes()
    try:
        user = await hub.user()
    except hub.HubError as e:
        raise HTTPException(status_code=503, detail=str(e))
    models = user.get("servers", {})

    entries = []
    for defaults in defaults_for_nodes(db, list(nodes.values())):
        model = models.get(defaults.node)
        state = _state(model)
        placement = _placement_of(model) if state != "stopped" else {}
        entries.append(
            NodeServer(
                node=defaults.node,
                server_name=defaults.node,
                state=state,
                cpu_cores=placement.get("cpu_cores", defaults.cpu_cores),
                memory_gb=placement.get("memory_gb", defaults.memory_gb),
                gpus=placement.get("gpus", defaults.gpus),
                defaults={"cpu_cores": defaults.cpu_cores, "memory_gb": defaults.memory_gb, "gpus": defaults.gpus},
                capacity=defaults.capacity.model_dump(),
                gpus_free=int(nodes[defaults.node]["available"].get("gpu", 0)),
                url=_url(model),
                last_activity=(model or {}).get("last_activity"),
                start_error=_start_failures.get(defaults.node) if state == "stopped" else None,
            )
        )
    running = [e for e in entries if e.state == "running"]
    for entry, details in zip(running, await asyncio.gather(*(_running_details(e.server_name) for e in running))):
        for key, value in details.items():
            setattr(entry, key, value)

    others = []
    for name, model in models.items():
        if not name.startswith(JOB_SERVER_PREFIX):
            continue
        state = _state(model)
        if state == "stopped":
            continue
        others.append(
            OtherServer(
                server_name=name,
                kind="unattended-run",
                state=state,
                url=_url(model),
                **_placement_of(model),
            )
        )

    if running:
        message = "Notebook servers are running on " + ", ".join(f"{e.node} ({e.gpus} GPU)" for e in running) + "."
    else:
        message = "No notebook server is running. start_notebook_server starts one on the node you choose."
    return ServersStatus(servers=entries, other_servers=others, message=message)


@router.get("/servers", response_model=ServersStatus, operation_id="jupyter_notebook_status")
async def jupyter_notebook_status(current_user: dict = Depends(get_current_user_dual_auth), db: Session = Depends(get_db)):
    """Every node's notebook server: running or not, its CPU, memory and GPUs, the kernels open on it, and whether its tools answer."""
    return await _servers_status(db)


async def _node_status(db: Session, node: str) -> NodeServer:
    status = await _servers_status(db)
    return next(s for s in status.servers if s.node == node)


async def watch_start(node: str) -> None:
    """Wait for a requested start to finish, and keep the reason when the Hub gives up on it."""
    try:
        await hub.wait_ready(node, timeout=600.0)
    except hub.HubError as e:
        _start_failures[node] = str(e)
        logger.warning("notebook server on %s did not start: %s", node, e)
    finally:
        _start_watchers.pop(node, None)


class StartServerRequest(BaseModel):
    cpu_cores: Union[int, str, None] = Field(None, description="CPU cores; defaults to the node's default.")
    memory_gb: Union[int, str, None] = Field(None, description="Memory in GB; defaults to the node's default.")
    gpus: Union[int, str, None] = Field(None, description="GPUs to reserve, 0 for none; defaults to the node's default. On a node that shares its GPUs in time slices, 1 or every GPU of the node.")


@router.post("/servers/{node}/start", response_model=NodeServer, operation_id="start_notebook_server")
async def start_notebook_server(
    node: str,
    request: Optional[StartServerRequest] = None,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Start the notebook server on a node, for example 'tkspark', with the node's default CPU, memory and GPUs unless others are given. Answers at once with state 'starting'; poll jupyter_notebook_status until the node's state is 'running' (its tools answer when extension.status is 'ok') or 'stopped' with start_error. Servers on other nodes keep running."""
    request = request or StartServerRequest()
    placement = await resolve_placement(
        db,
        node,
        jupyter_notebooks._opt_int(request.cpu_cores, "cpu_cores"),
        jupyter_notebooks._opt_int(request.memory_gb, "memory_gb"),
        jupyter_notebooks._opt_int(request.gpus, "gpus"),
    )
    try:
        current = await hub.server(node)
        if current and (current.get("ready") or current.get("pending")):
            where = _placement_of(current)
            raise HTTPException(
                status_code=409,
                detail=f"The notebook server on {node} is already {_state(current)} with {where['gpus']} GPU(s); stop_notebook_server first.",
            )
        _start_failures.pop(node, None)
        await hub.start_server(node, placement.user_options())
    except hub.HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    _start_watchers[node] = asyncio.create_task(watch_start(node))
    return await _node_status(db, node)


@router.post("/servers/{node}/stop", response_model=ServersStatus, operation_id="stop_notebook_server")
async def stop_notebook_server(node: str, current_user: dict = Depends(get_current_user_dual_auth), db: Session = Depends(get_db)):
    """Stop the notebook server on a node and free its memory and GPUs. Its kernels are shut down; notebook files keep their outputs. Answers as soon as the Hub has the request; the node's state is 'stopping' until jupyter_notebook_status shows 'stopped'."""
    nodes = await _nodes()
    if node not in nodes:
        raise HTTPException(status_code=422, detail=f"node '{node}' is not in the cluster; the nodes are {sorted(nodes)}")
    try:
        await hub.stop_server(node)
    except hub.HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return await _servers_status(db)


# ---------------------------------------------------------------------------
# Unattended runs
# ---------------------------------------------------------------------------

class RunNotebookJobRequest(BaseModel):
    notebook_path: Optional[str] = Field(None, description="Path of the notebook, relative to the notebooks folder. Give this or notebook.")
    notebook: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "The notebook itself, as nbformat JSON, for a caller that cannot write into the notebooks folder. "
            "Give this or notebook_path."
        ),
    )
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Values for the run, any JSON object. A first cell tagged 'parameters' sets them as the dict "
            "PARAMETERS in the kernel."
        ),
    )
    kernel_name: Optional[str] = Field(None, description="Kernel to run it with, for example 'fine-tuning'. Defaults to the one the notebook names.")
    node: str = Field(..., description="The node to run on, for example 'tkspark'.")
    cpu_cores: Union[int, str, None] = Field(None, description="CPU cores for the run.")
    memory_gb: Union[int, str, None] = Field(None, description="Memory in GB for the run.")
    gpus: Union[int, str, None] = Field(None, description="GPUs to reserve for the run, 0 for none. On a node that shares its GPUs in time slices, 1 or every GPU of the node (for a run on several GPUs).")
    stop_on_error: Union[bool, str, None] = Field(True, description="Stop at the first cell that raises (default true).")
    cell_timeout_seconds: Union[int, str, None] = Field(None, description="Seconds allowed per cell; default 3600.")
    reserve_memory: Union[bool, str, None] = Field(
        False,
        description=(
            "Reserve all of memory_gb on the node (default false: a quarter is reserved and the rest is "
            "used when free). For a long run that must not be killed for memory, such as a training."
        ),
    )
    training: Union[bool, str, None] = Field(
        False,
        description=(
            "The run is a training (default false): it gets the node's training folder at "
            "/home/thinkube/training, kept across runs, and is let onto the node training mode keeps free. "
            "Training mode must be on for this node; when it is off, or on for another node, the run is "
            "refused. A run for the node training mode is on gets the same without asking."
        ),
    )


class JobResponse(BaseModel):
    job: Dict[str, Any]


# The volume claim of the notebooks folder, and the folder in it that holds
# the notebook each job with a given notebook or parameters runs. The folder
# is not hidden: the Jupyter server does not open hidden paths.
NOTEBOOKS_CLAIM = "jupyterhub/jupyterhub-notebooks-pvc"
JOB_NOTEBOOKS_FOLDER = "job-runs"


def _parameters_cell(parameters: Dict[str, Any]) -> Dict[str, Any]:
    text = json.dumps(json.dumps(parameters))
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {"tags": ["parameters"]},
        "outputs": [],
        "source": f"import json\nPARAMETERS = json.loads({text})",
    }


def _job_notebook(root: Path, job_id: uuid.UUID, notebook_path: Optional[str],
                  notebook: Optional[Dict[str, Any]], parameters: Optional[Dict[str, Any]]) -> str:
    """Writes the notebook a job runs into JOB_NOTEBOOKS_FOLDER of the notebooks
    folder `root`: the given notebook, or a copy of the one at notebook_path,
    with the parameters cell first when there are parameters. The source
    notebook is left as it is. Returns the path relative to `root`."""
    if notebook is None:
        given = PurePosixPath(notebook_path)
        source = root.joinpath(*given.parts)
        if given.is_absolute() or any(p in ("..", ".") for p in given.parts) or not source.is_file():
            raise HTTPException(status_code=422, detail=f"notebook_path {notebook_path!r} is not a notebook in the notebooks folder")
        try:
            notebook = json.loads(source.read_text())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"{notebook_path} is not a notebook: {e}")
    if not isinstance(notebook.get("cells"), list) or not isinstance(notebook.get("nbformat"), int):
        raise HTTPException(status_code=422, detail="notebook must be nbformat JSON with 'cells' and 'nbformat'")
    if parameters:
        notebook = {**notebook, "cells": [_parameters_cell(parameters)] + notebook["cells"]}
    folder = root / JOB_NOTEBOOKS_FOLDER
    folder.mkdir(exist_ok=True)
    target = folder / f"{job_id}.ipynb"
    target.write_text(json.dumps(notebook, indent=1))
    return f"{JOB_NOTEBOOKS_FOLDER}/{target.name}"


def _job_session() -> Session:
    return SessionLocal()()


def _update_job(job_id: uuid.UUID, **fields: Any) -> Optional[Dict[str, Any]]:
    db = _job_session()
    try:
        job = db.query(NotebookJob).filter(NotebookJob.id == job_id).first()
        if job is None:
            return None
        for key, value in fields.items():
            setattr(job, key, value)
        if job.status not in ("starting", "running") and job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
        return job.to_dict()
    finally:
        db.close()


def _read_job(job_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    db = _job_session()
    try:
        job = db.query(NotebookJob).filter(NotebookJob.id == job_id).first()
        return job.to_dict() if job else None
    finally:
        db.close()


async def _remove_job_server(job_id: uuid.UUID, server_name: str) -> None:
    """Remove a finished job's server from the Hub. A server left behind keeps
    counting toward the Hub's limit of named servers, so a failure is written
    on the job, where the person reading it sees what is left."""
    try:
        await hub.remove_server(server_name)
    except hub.HubError as e:
        logger.error("could not remove job server %s: %s", server_name, e)
        current = _read_job(job_id) or {}
        said = f"the job's server {server_name} was not removed: {e}"
        _update_job(job_id, error=f"{current['error']}; {said}" if current.get("error") else said)


async def _remove_leftover_job_servers() -> None:
    """Remove job servers that belong to no open job: a job that ended while
    thinkube-control could not remove its server leaves one behind."""
    db = _job_session()
    try:
        open_servers = {
            j.server_name
            for j in db.query(NotebookJob).filter(NotebookJob.status.in_(["starting", "running"])).all()
        }
    finally:
        db.close()
    for name in (await hub.user(include_stopped=True)).get("servers", {}):
        if name.startswith(JOB_SERVER_PREFIX) and name not in open_servers:
            await hub.remove_server(name)
            logger.info("removed the leftover job server %s", name)


def _end_job_on_error(job_id: uuid.UUID, e: Exception) -> None:
    if isinstance(e, HTTPException):
        logger.error("notebook job %s failed: %s", job_id, e.detail)
        _update_job(job_id, status="error", error=str(e.detail))
    elif isinstance(e, hub.HubError):
        logger.error("notebook job %s failed: %s", job_id, e)
        _update_job(job_id, status="error", error=str(e))
    else:
        logger.error("notebook job %s failed: %s", job_id, e, exc_info=True)
        _update_job(job_id, status="error", error=f"{type(e).__name__}: {e}")


async def _run_job(job_id: uuid.UUID, server_name: str, placement: Placement, notebook_path: str, kernel_name: Optional[str], stop_on_error: bool, cell_timeout: Optional[int]) -> None:
    try:
        await _remove_leftover_job_servers()
        await hub.start_server(server_name, placement.user_options())
        await hub.wait_ready(server_name)
        await _open_and_run(job_id, server_name, notebook_path, kernel_name, placement.gpus > 0, stop_on_error, cell_timeout)
    except Exception as e:
        _end_job_on_error(job_id, e)
    finally:
        await _release_job(job_id, server_name)


async def _release_job(job_id: uuid.UUID, server_name: str) -> None:
    """Remove the server of a job that has ended. A job still open keeps its
    server: its task ends with the thinkube-control process, and
    resume_notebook_jobs follows the run again on that server."""
    _job_tasks.pop(str(job_id), None)
    current = _read_job(job_id) or {}
    if current.get("status") not in ("starting", "running"):
        await _remove_job_server(job_id, server_name)


async def _open_and_run(job_id: uuid.UUID, server_name: str, notebook_path: str, kernel_name: Optional[str], needs_gpu: bool, stop_on_error: bool, cell_timeout: Optional[int]) -> None:
    """On the job's ready server: open the notebook, start the run of all its cells, and follow it to its end."""
    health = None
    for _ in range(30):
        try:
            health = await jupyter_notebooks.extension_health(server_name)
        except HTTPException:
            health = None
        if health and health.get("status") == "ok":
            break
        await asyncio.sleep(3)
    if not health or health.get("status") != "ok":
        raise RuntimeError("the job's server started but tk-notebook-mcp did not answer")

    use_args: Dict[str, Any] = {"notebook_path": notebook_path, "needs_gpu": needs_gpu}
    if kernel_name:
        use_args["kernel_name"] = kernel_name
    opened = await jupyter_notebooks.call_tool("use_notebook", use_args, timeout=jupyter_notebooks.OPEN_TIMEOUT, server_name=server_name)
    if not opened.get("success"):
        raise RuntimeError(opened.get("error") or "use_notebook failed")

    run_args: Dict[str, Any] = {"notebook_path": notebook_path, "stop_on_error": stop_on_error}
    if cell_timeout is not None:
        run_args["cell_timeout_seconds"] = cell_timeout
    started = await jupyter_notebooks.call_tool("execute_all_cells", run_args, server_name=server_name)
    if not started.get("success"):
        raise RuntimeError(started.get("error") or "execute_all_cells failed")
    _update_job(job_id, status="running", execution_id=started["execution_id"], total_cells=started.get("total_cells"), kernel_name=opened.get("kernel_name"))

    await _poll_job(job_id, server_name, started["execution_id"])


# The statuses tk-notebook-mcp gives a run that has ended.
FINAL_RUN_STATES = ("completed", "error", "lost")


async def _server_gone(server_name: str) -> bool:
    """True when the Hub no longer lists the job's server. A Hub that does not
    answer says nothing about the server, so that is not taken as gone."""
    try:
        return await hub.server(server_name) is None
    except hub.HubError as e:
        logger.warning("could not ask the Hub about job server %s: %s", server_name, e)
        return False


async def _poll_job(job_id: uuid.UUID, server_name: str, execution_id: str) -> None:
    """Follow a run until tk-notebook-mcp gives it a final status, or the Hub
    no longer lists its server. A status call that fails or is slow is not an
    end: the next poll asks again."""
    while True:
        await asyncio.sleep(JOB_POLL_SECONDS)
        current = _read_job(job_id)
        if current is None or current["status"] == "cancelled":
            return
        try:
            state = await jupyter_notebooks.call_tool("check_all_cells_status", {"execution_id": execution_id}, server_name=server_name)
        except HTTPException as e:
            if await _server_gone(server_name):
                _update_job(job_id, status="lost", error=f"the job's server {server_name} is gone; the Hub no longer lists it (last status call: {e.detail})")
                return
            logger.warning("notebook job %s: the status call failed, asking again in %d s: %s", job_id, JOB_POLL_SECONDS, e.detail)
            continue
        fields = {
            "completed_cells": state.get("completed_cells"),
            "total_cells": state.get("total_cells"),
            "failed_cell_index": state.get("failed_cell_index"),
            "results": state.get("results"),
        }
        status = state.get("status")
        if status == "running":
            _update_job(job_id, **fields)
            continue
        if status not in FINAL_RUN_STATES:
            logger.warning("notebook job %s: the status answer has no final status, asking again in %d s: %s",
                           job_id, JOB_POLL_SECONDS, state.get("error") or state)
            continue
        final = "completed" if status == "completed" else "error"
        _update_job(job_id, status=final, error=state.get("error"), **fields)
        return


@router.post("/jobs", response_model=JobResponse, operation_id="run_notebook_job")
async def run_notebook_job(
    request: RunNotebookJobRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Run a whole notebook unattended on its own server: started on the node with the GPUs asked for (the node's defaults fill the rest), run top to bottom, stopped when done. Returns a job_id at once; poll notebook_job_status. The notebook file keeps the outputs. A training sets training true and runs on the node training mode is on. With a given notebook or parameters, the job runs its own copy, job-runs/<job_id>.ipynb in the notebooks folder, which keeps the outputs; parameters reach the notebook as the dict PARAMETERS."""
    is_training = jupyter_notebooks._bool(request.training, False)
    training = await training_mode.status()
    if is_training and training is None:
        raise HTTPException(
            status_code=409,
            detail=f"training is true but training mode is off; turn training mode on for {request.node} first (training_mode_on with node '{request.node}'), then run the job",
        )
    if is_training and training["node"] != request.node:
        raise HTTPException(
            status_code=409,
            detail=f"training is true but training mode is on for {training['node']}, not {request.node}; a training runs on the node training mode is on",
        )
    placement = await resolve_placement(
        db,
        request.node,
        jupyter_notebooks._opt_int(request.cpu_cores, "cpu_cores"),
        jupyter_notebooks._opt_int(request.memory_gb, "memory_gb"),
        jupyter_notebooks._opt_int(request.gpus, "gpus"),
    )
    placement.reserve_memory = jupyter_notebooks._bool(request.reserve_memory, False)
    placement.training_node = training is not None and training["node"] == placement.node
    if request.kernel_name:
        await _check_kernel_fits_node(db, request.kernel_name, placement.node)
    stop_on_error = jupyter_notebooks._bool(request.stop_on_error, True)
    cell_timeout = jupyter_notebooks._opt_int(request.cell_timeout_seconds, "cell_timeout_seconds")
    if (request.notebook_path is None) == (request.notebook is None):
        raise HTTPException(status_code=422, detail="give one of notebook_path and notebook")
    job_id = uuid.uuid4()
    notebook_path = request.notebook_path
    if request.notebook is not None or request.parameters:
        try:
            root = await asyncio.to_thread(storage.claim_folder, NOTEBOOKS_CLAIM)
        except storage.StorageError as e:
            raise HTTPException(status_code=503, detail=f"the notebooks folder: {e}")
        notebook_path = _job_notebook(root, job_id, request.notebook_path, request.notebook, request.parameters)
    job = NotebookJob(
        id=job_id,
        server_name=f"{JOB_SERVER_PREFIX}{uuid.uuid4().hex[:8]}",
        notebook_path=notebook_path,
        kernel_name=request.kernel_name,
        node=placement.node,
        cpu_cores=placement.cpu_cores,
        memory_gb=placement.memory_gb,
        gpus=placement.gpus,
        stop_on_error=stop_on_error,
        cell_timeout_seconds=cell_timeout,
        training=placement.training_node,
        status="starting",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    task = asyncio.create_task(
        _run_job(
            job.id,
            job.server_name,
            placement,
            notebook_path,
            request.kernel_name,
            stop_on_error,
            cell_timeout,
        )
    )
    _job_tasks[str(job.id)] = task
    return JobResponse(job=job.to_dict())


@router.get("/jobs", response_model=List[Dict[str, Any]], operation_id="list_notebook_jobs")
async def list_notebook_jobs(current_user: dict = Depends(get_current_user_dual_auth), db: Session = Depends(get_db)):
    """The unattended notebook runs, newest first."""
    jobs = db.query(NotebookJob).order_by(NotebookJob.created_at.desc()).limit(50).all()
    return [j.to_dict() for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobResponse, operation_id="notebook_job_status")
async def notebook_job_status(job_id: uuid.UUID, current_user: dict = Depends(get_current_user_dual_auth)):
    """Progress of an unattended run: starting, running (cells done of total), completed, error or cancelled, with each cell's result."""
    job = _read_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return JobResponse(job=job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse, operation_id="cancel_notebook_job")
async def cancel_notebook_job(job_id: uuid.UUID, current_user: dict = Depends(get_current_user_dual_auth)):
    """Stop an unattended run and its server. Cells already run keep their outputs."""
    job = _read_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if job["status"] not in ("starting", "running"):
        return JobResponse(job=job)
    updated = _update_job(job_id, status="cancelled", error="cancelled by request")
    task = _job_tasks.pop(str(job_id), None)
    if task:
        task.cancel()
    await _remove_job_server(job_id, job["server_name"])
    return JobResponse(job=_read_job(job_id) or updated or job)


# How often, JOB_POLL_SECONDS apart, a resumed job asks the Hub whether its server exists.
RESUME_TRIES = 20


async def resume_notebook_jobs() -> None:
    """After a restart of thinkube-control: every open run whose server the Hub
    still lists is followed again, whatever its recorded status; a run whose
    server is gone is marked lost. Each job is resumed in its own task."""
    db = _job_session()
    try:
        open_jobs = db.query(NotebookJob).filter(NotebookJob.status.in_(["starting", "running"])).all()
        records = [j.to_dict() for j in open_jobs]
    finally:
        db.close()
    for record in records:
        _job_tasks[record["job_id"]] = asyncio.create_task(_resume_job(record))


async def _resume_job(record: Dict[str, Any]) -> None:
    job_id = uuid.UUID(record["job_id"])
    server_name = record["server_name"]
    try:
        model, said = None, None
        for attempt in range(RESUME_TRIES):
            try:
                model = await hub.server(server_name)
                said = None
                break
            except hub.HubError as e:
                said = e
                logger.warning("notebook job %s: the Hub did not say whether %s exists (try %d of %d): %s",
                               job_id, server_name, attempt + 1, RESUME_TRIES, e)
                await asyncio.sleep(JOB_POLL_SECONDS)
        if said is not None:
            _update_job(job_id, status="error",
                        error=f"after a restart of thinkube-control the Hub did not say whether the job's server {server_name} exists: {said}")
            return
        if model is None:
            _update_job(job_id, status="lost",
                        error=f"the job's server {server_name} was gone when thinkube-control restarted")
            return
        if not model.get("ready"):
            await hub.wait_ready(server_name)
        logger.info("resumed notebook job %s on %s", job_id, server_name)
        if record.get("execution_id"):
            await _poll_job(job_id, server_name, record["execution_id"])
            return
        # The run was not started yet, or was started in the moment before its
        # execution id was recorded; in that case the kernel is held by the run
        # and execute_all_cells answers that it is busy.
        if record.get("stop_on_error") is None:
            raise RuntimeError("the job was recorded without stop_on_error, so its run cannot be started again after the restart")
        await _open_and_run(job_id, server_name, record["notebook_path"], record.get("kernel_name"),
                            (record.get("gpus") or 0) > 0, record["stop_on_error"], record.get("cell_timeout_seconds"))
    except Exception as e:
        _end_job_on_error(job_id, e)
    finally:
        await _release_job(job_id, server_name)
