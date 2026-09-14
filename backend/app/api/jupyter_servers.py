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
import logging
import uuid
from datetime import datetime, timezone
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
from app.models.notebook_jobs import NotebookJob
from app.services import jupyterhub_client as hub
from app.services import notebook_resources as res

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyter", tags=["jupyter-server"])

PROFILE = "thinkube-notebooks"
JOB_POLL_SECONDS = 15
JOB_SERVER_PREFIX = jupyter_notebooks.JOB_SERVER_PREFIX
HUB_DEFAULT = jupyter_notebooks.HUB_DEFAULT

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

    def user_options(self) -> Dict[str, str]:
        return {
            "profile": PROFILE,
            "node": self.node,
            "cpu": str(self.cpu_cores),
            "memory": f"{self.memory_gb}G",
            "enable_gpu": str(self.gpus),
        }


async def _nodes() -> Dict[str, Dict[str, Any]]:
    nodes = {n["name"]: n for n in await get_cluster_resources()}
    if not nodes:
        raise HTTPException(status_code=503, detail="The cluster's node resources are not known yet; try again in a moment.")
    return nodes


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
    if placement.gpus < 0 or placement.gpus > capacity["gpus"]:
        problems.append(f"gpus must be between 0 and {capacity['gpus']} on {node}" if capacity["gpus"] else f"{node} has no GPU")
    elif placement.gpus > free_gpus:
        problems.append(
            f"{node} has {free_gpus} GPU slots free of {int(info['capacity'].get('gpu', 0))}; "
            "a served model may be holding the rest (unload_llm_model frees one)"
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
        "gpus": int(options["enable_gpu"]) if str(options.get("enable_gpu", "")).isdigit() else None,
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
    server_name: str = Field(..., description="'default' for the Hub's default server, or an unattended run's server.")
    kind: str = Field(..., description="'hub-default' or 'unattended-run'.")
    state: str
    node: Optional[str] = None
    cpu_cores: Optional[int] = None
    memory_gb: Optional[int] = None
    gpus: Optional[int] = None
    url: Optional[str] = None


class ServersStatus(BaseModel):
    servers: List[NodeServer] = Field(..., description="One entry per node.")
    other_servers: List[OtherServer] = Field(default_factory=list, description="The Hub's default server and the servers of unattended runs.")
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
        if name in nodes:
            continue
        if name and not name.startswith(JOB_SERVER_PREFIX):
            continue
        state = _state(model)
        if state == "stopped":
            continue
        others.append(
            OtherServer(
                server_name=name or HUB_DEFAULT,
                kind="unattended-run" if name else "hub-default",
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
    gpus: Union[int, str, None] = Field(None, description="GPUs to reserve, 0 for none; defaults to the node's default.")


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
    """Stop the notebook server on a node ('default' stops the Hub's default server) and free its memory and GPUs. Its kernels are shut down; notebook files keep their outputs. Answers as soon as the Hub has the request; the node's state is 'stopping' until jupyter_notebook_status shows 'stopped'."""
    name = "" if node == HUB_DEFAULT else node
    if name.startswith(JOB_SERVER_PREFIX):
        raise HTTPException(status_code=422, detail="That server belongs to an unattended run; cancel_notebook_job stops it.")
    try:
        await hub.stop_server(name)
    except hub.HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return await _servers_status(db)


# ---------------------------------------------------------------------------
# Unattended runs
# ---------------------------------------------------------------------------

class RunNotebookJobRequest(BaseModel):
    notebook_path: str = Field(..., description="Path of the notebook, relative to the notebooks folder.")
    kernel_name: Optional[str] = Field(None, description="Kernel to run it with, for example 'fine-tuning'. Defaults to the one the notebook names.")
    node: str = Field(..., description="The node to run on, for example 'tkspark'.")
    cpu_cores: Union[int, str, None] = Field(None, description="CPU cores for the run.")
    memory_gb: Union[int, str, None] = Field(None, description="Memory in GB for the run.")
    gpus: Union[int, str, None] = Field(None, description="GPUs to reserve for the run, 0 for none.")
    stop_on_error: Union[bool, str, None] = Field(True, description="Stop at the first cell that raises (default true).")
    cell_timeout_seconds: Union[int, str, None] = Field(None, description="Seconds allowed per cell; default 3600.")


class JobResponse(BaseModel):
    job: Dict[str, Any]


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


async def _stop_job_server(server_name: str) -> None:
    try:
        await hub.stop_server(server_name)
        await hub.wait_stopped(server_name)
    except hub.HubError as e:
        logger.warning("could not stop job server %s: %s", server_name, e)


async def _run_job(job_id: uuid.UUID, server_name: str, placement: Placement, notebook_path: str, kernel_name: Optional[str], stop_on_error: bool, cell_timeout: Optional[int]) -> None:
    try:
        await hub.start_server(server_name, placement.user_options())
        await hub.wait_ready(server_name)
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

        use_args: Dict[str, Any] = {"notebook_path": notebook_path, "needs_gpu": placement.gpus > 0}
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
    except HTTPException as e:
        logger.error("notebook job %s failed: %s", job_id, e.detail)
        _update_job(job_id, status="error", error=str(e.detail))
    except Exception as e:
        logger.error("notebook job %s failed: %s", job_id, e, exc_info=True)
        _update_job(job_id, status="error", error=f"{type(e).__name__}: {e}")
    finally:
        await _stop_job_server(server_name)
        _job_tasks.pop(str(job_id), None)


async def _poll_job(job_id: uuid.UUID, server_name: str, execution_id: str) -> None:
    while True:
        await asyncio.sleep(JOB_POLL_SECONDS)
        current = _read_job(job_id)
        if current is None or current["status"] == "cancelled":
            return
        state = await jupyter_notebooks.call_tool("check_all_cells_status", {"execution_id": execution_id}, server_name=server_name)
        fields = {
            "completed_cells": state.get("completed_cells"),
            "total_cells": state.get("total_cells"),
            "failed_cell_index": state.get("failed_cell_index"),
            "results": state.get("results"),
        }
        if state.get("status") == "running":
            _update_job(job_id, **fields)
            continue
        final = "completed" if state.get("status") == "completed" else "error"
        _update_job(job_id, status=final, error=state.get("error"), **fields)
        return


@router.post("/jobs", response_model=JobResponse, operation_id="run_notebook_job")
async def run_notebook_job(
    request: RunNotebookJobRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Run a whole notebook unattended on its own server: started on the node with the GPUs asked for (the node's defaults fill the rest), run top to bottom, stopped when done. Returns a job_id at once; poll notebook_job_status. The notebook file keeps the outputs."""
    placement = await resolve_placement(
        db,
        request.node,
        jupyter_notebooks._opt_int(request.cpu_cores, "cpu_cores"),
        jupyter_notebooks._opt_int(request.memory_gb, "memory_gb"),
        jupyter_notebooks._opt_int(request.gpus, "gpus"),
    )
    job = NotebookJob(
        server_name=f"{JOB_SERVER_PREFIX}{uuid.uuid4().hex[:8]}",
        notebook_path=request.notebook_path,
        kernel_name=request.kernel_name,
        node=placement.node,
        cpu_cores=placement.cpu_cores,
        memory_gb=placement.memory_gb,
        gpus=placement.gpus,
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
            request.notebook_path,
            request.kernel_name,
            jupyter_notebooks._bool(request.stop_on_error, True),
            jupyter_notebooks._opt_int(request.cell_timeout_seconds, "cell_timeout_seconds"),
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
    await _stop_job_server(job["server_name"])
    return JobResponse(job=updated or job)


async def resume_notebook_jobs() -> None:
    """After a restart of thinkube-control: pick polling up again for runs still on a server, mark the rest lost."""
    db = _job_session()
    try:
        open_jobs = db.query(NotebookJob).filter(NotebookJob.status.in_(["starting", "running"])).all()
        records = [j.to_dict() for j in open_jobs]
    finally:
        db.close()
    for record in records:
        job_id = uuid.UUID(record["job_id"])
        server_name = record["server_name"]
        execution_id = record.get("execution_id")
        try:
            model = await hub.server(server_name)
        except hub.HubError:
            model = None
        if record["status"] == "running" and execution_id and model and model.get("ready"):
            async def resume(job_id=job_id, server_name=server_name, execution_id=execution_id):
                try:
                    await _poll_job(job_id, server_name, execution_id)
                finally:
                    await _stop_job_server(server_name)
            _job_tasks[str(job_id)] = asyncio.create_task(resume())
            logger.info("resumed polling notebook job %s on %s", job_id, server_name)
        else:
            _update_job(job_id, status="lost", error="thinkube-control restarted while the run was starting")
            await _stop_job_server(server_name)
