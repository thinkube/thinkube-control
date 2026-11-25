# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The jobs: a command the scheduler runs in a pod on a node, on the GPUs it chooses."""

import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import User, get_current_user
from app.db.session import get_db
from app.models.jobs import OPEN, RUNNING, STOPPED, STOPPING, WAITING, Job
from app.services import pods, worker

router = APIRouter()


class NewJob(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, description="what the job is, for the people who read "
                                                                    "the list")
    owner: str = Field("person", min_length=1, max_length=40, description="the service the job is of, such as "
                       "'forge' or 'mill'; 'person' for one a person gives")
    node: str = Field(..., description="the node it runs on")
    image: str = Field(..., description="its image, a path in the cluster's registry, such as "
                                        "'library/tk-jupyter-base:latest'")
    command: List[str] = Field(..., min_length=1, description="the command and its arguments")
    env: Dict[str, str] = Field(default_factory=dict, description="its environment variables")
    working_dir: Optional[str] = Field(None, description="the folder it starts in")
    mounts: List[str] = Field(default_factory=list, description=f"the folders it mounts, of {sorted(pods.MOUNTS)}: "
                              "'shared' the shared file system at /storage/thinkube-shared, 'models' the "
                              "notebooks' models folder at /home/thinkube/thinkube/models, 'mlflow' MLflow's "
                              "files at /home/thinkube/thinkube/mlflow (read-only), 'venvs' the node's Python "
                              "environments at /var/lib/jupyterhub-venvs (read-only)")
    gpus: int = Field(0, ge=0, le=16, description="how many GPUs of the node it needs")
    gpu_memory_gb: Optional[float] = Field(None, gt=0, description="the memory it takes on each GPU, with which "
                                           "it shares the GPU with other work that fits beside it; not given, it "
                                           "has its GPUs alone")
    memory_gb: int = Field(..., ge=1, le=1024, description="its memory")
    cpu_cores: int = Field(..., ge=1, le=256, description="its CPU cores")
    priority: int = Field(3, ge=1, le=5, description="1 (highest) to 5")
    resumable: bool = Field(False, description="whether it goes on where it stopped when started again, as a "
                                               "training with checkpoints does; else it starts over")
    stop_seconds: int = Field(60, ge=1, le=3600, description="the seconds it has to end after it is asked to stop")


def _job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return job


@router.post("/jobs", operation_id="scheduler_create_job")
async def create_job(request: NewJob, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Give the scheduler a job. It waits for its turn on its node and runs when the node has the GPUs it asks
    for; its status says where it is, and `waiting` what it waits for."""
    unknown = sorted(set(request.mounts) - set(pods.MOUNTS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"{unknown} are not folders a job mounts; they are "
                                                    f"{sorted(pods.MOUNTS)}")
    if request.gpu_memory_gb is not None and not request.gpus:
        raise HTTPException(status_code=422, detail="gpu_memory_gb is the memory taken on each GPU, and the job "
                                                    "asks for no GPU")
    job = Job(id=str(uuid.uuid4()), created_by=user.preferred_username, status=WAITING, starts=0,
              created_at=worker.utcnow(), **request.model_dump())
    db.add(job)
    db.commit()
    return job.to_dict()


@router.get("/jobs", operation_id="scheduler_list_jobs")
async def list_jobs(open_only: bool = Query(False, description="only the jobs that have not ended"),
                    limit: int = Query(100, ge=1, le=1000), user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """The jobs, the newest first, with the worker's state: when it last looked, and what failed if it did."""
    query = db.query(Job)
    if open_only:
        query = query.filter(Job.status.in_(OPEN))
    found = query.order_by(Job.created_at.desc()).limit(limit).all()
    last = worker.state["last_tick"]
    return {"jobs": [j.to_dict() for j in found],
            "worker": {"last_tick": last.isoformat() if last else None, "error": worker.state["error"]}}


@router.get("/jobs/{job_id}", operation_id="scheduler_get_job")
async def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One job."""
    return _job(db, job_id).to_dict()


@router.post("/jobs/{job_id}/stop", operation_id="scheduler_stop_job")
async def stop_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stop a job. A waiting one is stopped at once; a running one gets SIGTERM and its stop_seconds to end, and
    is stopped when its pod is gone."""
    job = _job(db, job_id)
    if job.status == WAITING:
        job.status, job.finished_at, job.waiting = STOPPED, worker.utcnow(), None
    elif job.status == RUNNING:
        job.status = STOPPING
    elif job.status == STOPPING:
        # Being stopped to make room, it would wait again; asked to stop, it ends
        job.preempted_by = None
    else:
        raise HTTPException(status_code=409, detail=f"job {job.name} is {job.status}; it has ended")
    db.commit()
    return job.to_dict()


@router.get("/jobs/{job_id}/log", operation_id="scheduler_get_job_log")
async def get_job_log(job_id: str, lines: int = Query(200, ge=1, le=5000), user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """The last lines the job's latest start wrote; null when its pod is gone (a day after it ended)."""
    job = _job(db, job_id)
    if job.pod_job is None:
        return {"log": None}
    try:
        return {"log": await pods.Pods().log(job.pod_job, lines)}
    except pods.PodsError as e:
        raise HTTPException(status_code=502, detail=str(e))
