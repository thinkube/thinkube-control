"""The notebook server itself, and unattended notebook runs.

A notebook server is a pod that JupyterHub starts on one node with a CPU,
memory and GPU budget chosen at start. These endpoints let Claude Code make
that choice: see what is running, start a server where the work needs to
be, stop it when the GPU should go back to serving models.

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
from app.core.api_tokens import get_current_user_dual_auth
from app.core.config import settings
from app.db.session import SessionLocal, get_db
from app.models.jupyterhub_config import JupyterHubConfig
from app.models.notebook_jobs import NotebookJob
from app.services import jupyterhub_client as hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyter", tags=["jupyter-server"])

PROFILE = "thinkube-ai-lab"
JOB_POLL_SECONDS = 15
JOB_SERVER_PREFIX = "job-"

_job_tasks: Dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def _gb(memory: str) -> float:
    """A Kubernetes memory string as gigabytes."""
    units = {"Ki": 1 / (1024 * 1024), "Mi": 1 / 1024, "Gi": 1, "Ti": 1024, "K": 1e3 / 2**30, "M": 1e6 / 2**30, "G": 1e9 / 2**30}
    for suffix, factor in units.items():
        if memory.endswith(suffix):
            return float(memory[: -len(suffix)]) * factor
    return float(memory) / 2**30


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


def _defaults(db: Session) -> JupyterHubConfig:
    config = db.query(JupyterHubConfig).first()
    if config is None:
        config = JupyterHubConfig(default_node=None, default_cpu_cores=4, default_memory_gb=8, default_gpu_count=0)
    return config


async def resolve_placement(
    db: Session,
    node: Optional[str],
    cpu_cores: Optional[int],
    memory_gb: Optional[int],
    gpus: Optional[int],
) -> Placement:
    """Fill what was not said from the JupyterHub defaults and check it against the node."""
    defaults = _defaults(db)
    nodes = {n["name"]: n for n in await get_cluster_resources()}
    if not nodes:
        raise HTTPException(status_code=503, detail="The cluster's node resources are not known yet; try again in a moment.")

    node = node or defaults.default_node
    if not node:
        raise HTTPException(
            status_code=422,
            detail=f"node is required and no default node is configured; the nodes are {sorted(nodes)}",
        )
    if node not in nodes:
        raise HTTPException(status_code=422, detail=f"node '{node}' is not in the cluster; the nodes are {sorted(nodes)}")
    info = nodes[node]

    placement = Placement(
        node=node,
        cpu_cores=cpu_cores if cpu_cores is not None else defaults.default_cpu_cores,
        memory_gb=memory_gb if memory_gb is not None else defaults.default_memory_gb,
        gpus=gpus if gpus is not None else defaults.default_gpu_count,
    )
    cap = info["capacity"]
    free = info["available"]
    problems = []
    if placement.cpu_cores < 1 or placement.cpu_cores > int(cap["cpu"]):
        problems.append(f"cpu_cores must be between 1 and {int(cap['cpu'])} on {node}")
    if placement.memory_gb < 1 or placement.memory_gb > int(_gb(cap["memory"])):
        problems.append(f"memory_gb must be between 1 and {int(_gb(cap['memory']))} on {node}")
    max_gpus = int(cap.get("effective_gpu", cap.get("gpu", 0)))
    if placement.gpus < 0 or placement.gpus > max_gpus:
        problems.append(f"gpus must be between 0 and {max_gpus} on {node}" if max_gpus else f"{node} has no GPU")
    elif placement.gpus > int(free.get("gpu", 0)):
        problems.append(
            f"{node} has {int(free.get('gpu', 0))} GPU slots free of {int(cap.get('gpu', 0))}; "
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


# ---------------------------------------------------------------------------
# Server status, start, stop
# ---------------------------------------------------------------------------

class ServerStatus(BaseModel):
    running: bool
    pending: Optional[str] = Field(None, description="'spawn' while starting, 'stop' while stopping, else null.")
    node: Optional[str] = None
    cpu_cores: Optional[int] = None
    memory_gb: Optional[int] = None
    gpus: Optional[int] = None
    url: Optional[str] = None
    last_activity: Optional[str] = None
    extension: Optional[Dict[str, Any]] = Field(None, description="tk-notebook-mcp health inside the server, when running.")
    named_servers: List[Dict[str, Any]] = Field(default_factory=list, description="Servers started for unattended runs.")
    message: str


async def _status() -> ServerStatus:
    try:
        user = await hub.user()
    except hub.HubError as e:
        raise HTTPException(status_code=503, detail=str(e))
    servers = user.get("servers", {})
    default = servers.get("")
    running = bool(default and default.get("ready"))
    placement = _placement_of(default)
    extension = None
    if running:
        try:
            extension = await jupyter_notebooks.extension_health()
        except HTTPException as e:
            extension = {"status": "unreachable", "error": e.detail}
    named = [
        {"server_name": name, "ready": s.get("ready"), "pending": s.get("pending"), **_placement_of(s)}
        for name, s in servers.items()
        if name
    ]
    if running:
        message = f"A notebook server is running on {placement['node']} with {placement['gpus']} GPU(s)."
    elif default and default.get("pending"):
        message = f"The notebook server is {default['pending']}ing."
    else:
        message = "No notebook server is running. start_notebook_server starts one on the node you choose."
    return ServerStatus(
        running=running,
        pending=default.get("pending") if default else None,
        url=f"https://notebooks.{settings.DOMAIN_NAME}{default['url']}" if default and default.get("url") else None,
        last_activity=default.get("last_activity") if default else None,
        extension=extension,
        named_servers=named,
        message=message,
        **placement,
    )


@router.get("/server", response_model=ServerStatus, operation_id="jupyter_notebook_status")
async def jupyter_notebook_status(current_user: dict = Depends(get_current_user_dual_auth)):
    """Whether a notebook server is running, on which node and with how many GPUs, and whether its tools answer."""
    return await _status()


class StartServerRequest(BaseModel):
    node: Optional[str] = Field(None, description="The node to run on, for example 'tkspark'. Defaults to the configured default node.")
    cpu_cores: Union[int, str, None] = Field(None, description="CPU cores; defaults to the JupyterHub default.")
    memory_gb: Union[int, str, None] = Field(None, description="Memory in GB; defaults to the JupyterHub default.")
    gpus: Union[int, str, None] = Field(None, description="GPUs to reserve, 0 for none; defaults to the JupyterHub default.")


@router.post("/server/start", response_model=ServerStatus, operation_id="start_notebook_server")
async def start_notebook_server(
    request: StartServerRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Start the notebook server on a node with the CPU, memory and GPUs asked for. Waits until it is ready. A server already running must be stopped first."""
    placement = await resolve_placement(
        db,
        request.node,
        jupyter_notebooks._opt_int(request.cpu_cores, "cpu_cores"),
        jupyter_notebooks._opt_int(request.memory_gb, "memory_gb"),
        jupyter_notebooks._opt_int(request.gpus, "gpus"),
    )
    try:
        current = await hub.server("")
        if current and (current.get("ready") or current.get("pending")):
            where = _placement_of(current)
            raise HTTPException(
                status_code=409,
                detail=f"A notebook server is already running on {where['node']} with {where['gpus']} GPU(s); stop_notebook_server first.",
            )
        await hub.start_server("", placement.user_options())
        await hub.wait_ready("")
    except hub.HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # The pod answers a moment after the Hub calls it ready.
    for _ in range(20):
        status = await _status()
        if status.extension and status.extension.get("status") == "ok":
            return status
        await asyncio.sleep(3)
    return await _status()


@router.post("/server/stop", response_model=ServerStatus, operation_id="stop_notebook_server")
async def stop_notebook_server(current_user: dict = Depends(get_current_user_dual_auth)):
    """Stop the notebook server and free its node, memory and GPUs. Kernels are shut down; notebook files keep their outputs."""
    try:
        await hub.stop_server("")
        await hub.wait_stopped("")
    except hub.HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return await _status()


# ---------------------------------------------------------------------------
# Unattended runs
# ---------------------------------------------------------------------------

class RunNotebookJobRequest(BaseModel):
    notebook_path: str = Field(..., description="Path of the notebook, relative to the notebooks folder.")
    kernel_name: Optional[str] = Field(None, description="Kernel to run it with, for example 'fine-tuning'. Defaults to the one the notebook names.")
    node: Optional[str] = Field(None, description="The node to run on. Defaults to the configured default node.")
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
    """Run a whole notebook unattended on its own server: started on the node with the GPUs asked for, run top to bottom, stopped when done. Returns a job_id at once; poll notebook_job_status. The notebook file keeps the outputs."""
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
