# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The scheduler's routes for the other parts of the platform, at /internal/scheduler, outside /api: the
extension's address sends only /api to the backend, so they are reached only at the Service, inside the cluster
(http://scheduler-backend:8000). They need no sign-in. A service that starts its own pods, such as the LLM gateway,
asks for its GPUs here in the background, with no user's token to pass on. They are operations of /api/v1."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import jobs
from app.db.session import get_db
from app.models.jobs import HOLD, JOB, OPEN, Job, NodeLimit
from app.services import nodes, priorities

router = APIRouter(prefix="/internal/scheduler", include_in_schema=False)


@router.get("/nodes")
async def list_nodes(db: Session = Depends(get_db)):
    try:
        found = await nodes.nodes.list()
    except nodes.NodesError as e:
        raise HTTPException(status_code=502, detail=str(e))
    limits = {limit.node: limit.gpus_at_work for limit in db.query(NodeLimit).all()}
    return {"nodes": [{**node, "gpus_at_work": limits.get(node["name"])} for node in found]}


@router.get("/priorities")
async def get_priorities(db: Session = Depends(get_db)):
    """{kind: priority}: the priority each part of the platform gives its work."""
    return priorities.table(db)


@router.post("/holds")
async def create_hold(request: jobs.NewHold, db: Session = Depends(get_db)):
    return jobs.create(db, request, HOLD, request.owner).to_dict()


@router.post("/jobs")
async def create_job(request: jobs.NewJob, db: Session = Depends(get_db)):
    """A job of a part of the platform, such as a Forge run's training, made in the background."""
    jobs.check_job(request)
    return jobs.create(db, request, JOB, request.owner).to_dict()


@router.get("/jobs/{job_id}/log")
async def job_log(job_id: str, lines: int = 200, db: Session = Depends(get_db)):
    return await jobs.job_log(jobs._job(db, job_id), lines)


@router.get("/jobs")
async def list_open_jobs(owner: str, db: Session = Depends(get_db)):
    """The jobs and holds of an owner that have not ended."""
    found = db.query(Job).filter(Job.owner == owner, Job.status.in_(OPEN)).order_by(Job.created_at).all()
    return {"jobs": [j.to_dict() for j in found]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, db: Session = Depends(get_db)):
    return jobs._job(db, job_id).to_dict()


@router.post("/jobs/{job_id}/stop")
async def stop_job(job_id: str, db: Session = Depends(get_db)):
    return jobs.end(db, jobs._job(db, job_id)).to_dict()


@router.post("/jobs/{job_id}/memory")
async def resize_hold(job_id: str, request: jobs.Memory, db: Session = Depends(get_db)):
    return (await jobs.resize(db, jobs._job(db, job_id), request.gpu_memory_gb)).to_dict()


@router.post("/jobs/{job_id}/release")
async def release_hold(job_id: str, db: Session = Depends(get_db)):
    return jobs.release(db, jobs._job(db, job_id)).to_dict()
