# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Task runs on a chosen node: jobs Mill gives the Scheduler (app/services/scheduled.py)."""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.runs import bearer, secret_values
from app.core.config import settings
from app.core.security import User, get_current_user
from app.db.session import get_db
from app.models.runs import MillJob, MillRun
from app.services import models as mirrored
from app.services import scheduled

router = APIRouter()

TAG_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")


class NewJob(BaseModel):
    """A task of the tasks repository, to run on a node when the Scheduler gives it its turn. The paths are
    relative to the root of the tasks repository, or absolute, as the main pod sees them."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=120, description="what the run is, for the people who read the "
                                                                    "lists")
    file: str = Field(..., min_length=1, description="the task's file, relative to the root of the tasks repository")
    task: Optional[str] = Field(None, description="the task's name, when the file declares several")
    inputs: Dict[str, str] = Field(default_factory=dict, description="the path of each input, by its name")
    outputs: Dict[str, str] = Field(default_factory=dict, description="the path of each output, by its name")
    settings: Dict[str, Any] = Field(default_factory=dict, description="values for the task's settings")
    limit: Optional[int] = Field(None, ge=1, description="read only the first N items of each jsonl input")
    node: str = Field(..., description="the node the task runs on")
    gpus: int = Field(0, ge=0, le=8, description="the GPUs of the node the task gets; 0 for none")
    gpu_memory_gb: Optional[float] = Field(None, gt=0, description="the memory the task takes on each GPU, with "
                                           "which it shares the GPU with other work that fits beside it; not given, "
                                           "it has its GPUs alone")
    memory_gb: int = Field(..., ge=1, le=1024, description="the memory the task's pod gets, in GiB")
    cpu_cores: int = Field(..., ge=1, le=256, description="the CPU cores the task's pod may use")
    priority: Optional[int] = Field(None, ge=1, le=5, description="1 (highest) to 5; the Scheduler's priority of "
                                                                  "Mill tasks when not given")
    venv: Optional[str] = Field(None, min_length=1, description="a Python environment of the notebook servers, by "
                                "its name, that runs the task, on their image; the AI base image's Python when not "
                                "given")
    models: List[str] = Field(default_factory=list, description="mirrored models the task reads, by their ids; "
                              "MILL_MODELS gives the task each one's folder")
    tags: Dict[str, str] = Field(default_factory=dict, description="names and values that link the run with what it "
                                 "is for, such as an experiment or a claim")
    secrets: List[str] = Field(default_factory=list, description="secrets of Thinkube Control, by their names; the "
                               "task gets each one's value as an environment variable of the same name")


def _failed(e: scheduled.SchedulerError) -> HTTPException:
    return HTTPException(status_code=502 if e.status >= 500 else 409, detail=e.detail)


async def _check_venv(token: Optional[str], name: str, gpus: int) -> None:
    """A venv a task can run in: built, and, when it needs a GPU, given one."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            r = await client.get(f"{settings.CONTROL_API_URL}/jupyter-venvs",
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Thinkube Control did not answer for its venvs: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Thinkube Control answered its venvs with {r.status_code}: "
                                                    f"{r.text[:300]}")
    venvs = [v for v in r.json()["venvs"] if not v.get("is_template")]
    found = next((v for v in venvs if v["name"] == name), None)
    if found is None:
        built = sorted(v["name"] for v in venvs if v.get("status") == "success")
        raise HTTPException(status_code=422, detail=f"venv: Thinkube Control has no venv {name}; the built ones are "
                                                    f"{', '.join(built) or 'none'}")
    if found.get("status") != "success":
        raise HTTPException(status_code=422, detail=f"venv: {name} is {found.get('status')}, not built")
    if found.get("needs_gpu") and not gpus:
        raise HTTPException(status_code=422, detail=f"venv: {name} needs a GPU, which Control keeps only on the GPU "
                                                    "nodes; give the task one")


def _runs(db: Session, job: MillJob) -> List[Dict[str, Any]]:
    """The runs of the job's starts, as the control center keeps them."""
    prefix = scheduled.run_prefix(job.id) + "-"
    found = db.query(MillRun).filter(MillRun.id.like(prefix + "%")).order_by(MillRun.started_at).all()
    return [{"id": r.id, "state": r.state, "started_at": r.started_at.isoformat()} for r in found]


def _view(db: Session, job: MillJob, at: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A job with what the Scheduler says of it now: its status, what it waits for, why it failed, its GPUs and
    how many times it started."""
    return {
        "id": job.id, "name": job.name, "node": job.node, "request": job.request, "tags": job.tags or {},
        "created_by": job.created_by, "created_at": job.created_at.isoformat(),
        "scheduler_id": job.scheduler_id,
        "status": at["status"] if at else "unknown", "waiting": (at or {}).get("waiting"),
        "error": (at or {}).get("error"), "gpu_ids": (at or {}).get("gpu_ids"), "starts": (at or {}).get("starts"),
        "priority": (at or {}).get("priority"), "runs": _runs(db, job),
    }


def _job(db: Session, job_id: str) -> MillJob:
    job = db.get(MillJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return job


@router.post("/jobs", operation_id="mill_run_on_node")
async def run_on_node(new: NewJob, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                      token: Optional[str] = Depends(bearer)) -> Dict[str, Any]:
    """Run a task on a chosen node, with or without GPUs, when its turn comes: Mill gives it to the Scheduler,
    which weighs it against all the work on the node by its priority. Each start is a run of Mill of its own,
    with its bars and samples in the control center (the job's runs). When work of a higher priority needs its
    GPUs, the task is stopped (SIGTERM, then a minute to end) and starts again from the beginning when its turn
    comes; a task that goes on where it stopped keeps its state in MILL_STATE_DIR and reads MILL_ATTEMPT. The task
    is given the LLM Gateway's address as LLM_GATEWAY_URL, your token as THINKUBE_API_TOKEN, and each secret of
    Thinkube Control named in `secrets` as an environment variable of its name."""
    for name in new.tags:
        if not TAG_NAME.match(name):
            raise HTTPException(status_code=422, detail=f"a tag's name is lowercase letters, digits and _.-, "
                                                        f"starting with a letter: {name!r}")
    try:
        known = await scheduled.nodes()
        priority = new.priority if new.priority is not None else await scheduled.priority()
    except scheduled.SchedulerError as e:
        raise _failed(e)
    if new.node not in known:
        raise HTTPException(status_code=422, detail=f"{new.node} is not a node of the cluster; the nodes are "
                                                    f"{', '.join(sorted(known))}")
    if new.gpu_memory_gb is not None and not new.gpus:
        raise HTTPException(status_code=422, detail="gpu_memory_gb is the memory taken on each GPU, and the task asks "
                                                    "for no GPU")
    if new.venv is not None:
        await _check_venv(token, new.venv, new.gpus)
    folders = {}
    for model in new.models:
        try:
            folders[model] = await mirrored.folder(model)
        except mirrored.ModelError as e:
            raise HTTPException(status_code=422, detail=f"models: {e}")
    job_id = str(uuid.uuid4())
    task = {"file": new.file, "task": new.task, "inputs": new.inputs, "outputs": new.outputs,
            "settings": new.settings, "limit": new.limit, "venv": new.venv}
    request = {"name": new.name, "task": task, "node": new.node, "gpus": new.gpus, "gpu_memory_gb": new.gpu_memory_gb,
               "memory_gb": new.memory_gb, "cpu_cores": new.cpu_cores, "models": folders, "secrets": new.secrets}
    secrets = await secret_values(token, new.secrets, scheduled.GIVEN_ENV)
    try:
        given = await scheduled.give(scheduled.job_spec(job_id, request, folders, priority, token, secrets))
    except scheduled.SchedulerError as e:
        raise _failed(e)
    job = MillJob(id=job_id, scheduler_id=given["id"], name=new.name, node=new.node,
                  request={**request, "priority": priority}, tags=new.tags or None, created_by=user.preferred_username,
                  created_at=datetime.now(timezone.utc))
    db.add(job)
    db.commit()
    return _view(db, job, given)


@router.get("/jobs", operation_id="mill_list_jobs")
async def list_jobs(limit: int = Query(20, ge=1, le=200, description="How many jobs to answer, the newest first."),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The task runs given to the Scheduler, the newest first: each with its status there (waiting, running,
    stopping, completed, failed, stopped), what it waits for, why it failed, and the runs of its starts."""
    found = db.query(MillJob).order_by(MillJob.created_at.desc()).limit(limit).all()
    answered = []
    for job in found:
        try:
            at = await scheduled.get(job.scheduler_id)
        except scheduled.SchedulerError as e:
            if e.status != 404:
                raise _failed(e)
            at = None
        answered.append(_view(db, job, at))
    return {"jobs": answered}


@router.get("/jobs/{job_id}", operation_id="mill_get_job")
async def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One task run given to the Scheduler, with its status there and the runs of its starts."""
    job = _job(db, job_id)
    try:
        return _view(db, job, await scheduled.get(job.scheduler_id))
    except scheduled.SchedulerError as e:
        raise _failed(e)


@router.get("/jobs/{job_id}/log", operation_id="mill_get_job_log")
async def get_job_log(job_id: str, lines: int = Query(200, ge=1, le=5000), user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """The last lines the pod of the job's latest start wrote, before its task reported to the control center
    too: installing thinkube-mill, waiting for NATS. Null when the pod is gone, a day after it ended."""
    try:
        return await scheduled.log(_job(db, job_id).scheduler_id, lines)
    except scheduled.SchedulerError as e:
        raise _failed(e)


@router.post("/jobs/{job_id}/stop", operation_id="mill_stop_job")
async def stop_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stop a task run given to the Scheduler: one that waits leaves the queue at once; a running one gets SIGTERM
    and a minute to end. It is not started again."""
    job = _job(db, job_id)
    try:
        return _view(db, job, await scheduled.stop(job.scheduler_id))
    except scheduled.SchedulerError as e:
        raise _failed(e)
