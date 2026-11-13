# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Models: the model catalogue, the mirrors that copy a catalogue model into
MLflow, and the publishing of fine-tuned models."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import User, get_current_user, oauth2_scheme
from app.db.session import get_db
from app.models.model_mirrors import ModelMirrorJob
from app.services import finetuned_models
from app.services.metadata import invalidate_catalogs
from app.services.model_downloader import ModelDownloaderService, MlflowDeleteError, catalog_entry, mlflow_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/models")

# Control's LLM Gateway answers within this; it reads the catalogue from this extension while it refreshes
CONTROL_TIMEOUT = httpx.Timeout(120.0)


class ModelInfo(BaseModel):
    id: str
    name: str
    size: Optional[str] = None
    quantization: str
    description: str
    server_type: List[str]
    is_downloaded: bool = False
    is_finetuned: bool = False
    task: str = "text-generation"
    params_b: Optional[float] = None
    active_params_b: Optional[float] = None
    context_length: Optional[int] = None
    reasoning_format: Optional[str] = None
    tool_use: bool = False
    license: Optional[str] = None
    gated: bool = False


class ModelCatalogResponse(BaseModel):
    models: List[ModelInfo]


class MirrorRequest(BaseModel):
    model_id: str = Field(..., description="The id of a catalogue model, such as Qwen/Qwen3.5-4B")


class MirrorResponse(BaseModel):
    job_id: str
    workflow_id: str
    model_id: str
    status: str
    message: str


class MirrorStatus(BaseModel):
    id: str
    model_id: str
    status: str
    workflow_name: Optional[str]
    workflow_url: Optional[str]
    error_message: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    is_running: bool
    is_complete: bool
    is_failed: bool


class MirrorJobsResponse(BaseModel):
    jobs: List[MirrorStatus]


class FinetunedModelRequest(BaseModel):
    catalog_entry: Dict[str, Any]


def estimate_size_from_params(params_b: float, quantization: str) -> str:
    """The weights' size, from the parameter count and the bytes per parameter of the quantization."""
    quant = (quantization or "BF16").upper()
    if "FP4" in quant or "NVFP4" in quant:
        bpp = 0.5
    elif "FP8" in quant:
        bpp = 1.0
    elif "BF16" in quant or "FP16" in quant or "F16" in quant:
        bpp = 2.0
    elif "Q4" in quant:
        bpp = 0.56
    elif "Q8" in quant:
        bpp = 1.0
    elif "1.58" in quant:
        bpp = 0.2
    else:
        bpp = 1.0
    return f"~{params_b * bpp:.0f}GB"


def catalogue_with_state() -> List[Dict[str, Any]]:
    """Every catalogue entry with is_downloaded, whether its mirror succeeded, and its size."""
    service = ModelDownloaderService()
    models = service.get_available_models()
    downloaded = service.check_all_models_exist()
    listed = []
    for model in models:
        if not model.get("size") and model.get("params_b"):
            model = {**model, "size": estimate_size_from_params(model["params_b"], model.get("quantization", "BF16"))}
        listed.append({**model, "is_downloaded": downloaded.get(model["id"], False)})
    return listed


@router.get("/catalog", response_model=ModelCatalogResponse, operation_id="models_get_catalog")
def get_model_catalog(current_user: User = Depends(get_current_user)):
    """The model catalogue: the platform's models.json merged with the user's, each
    model with is_downloaded (mirrored into MLflow), server_type, size and gated."""
    return ModelCatalogResponse(models=[ModelInfo(**m) for m in catalogue_with_state()])


@router.post("/mirrors", response_model=MirrorResponse, operation_id="models_mirror")
def submit_model_mirror(
    request: MirrorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mirror a catalogue model into MLflow. Answers at once with workflow_id; follow it
    with models_get_mirror (by workflow_id) until it has succeeded or failed. A model
    whose mirror is already running is refused with 409."""
    try:
        catalog_entry(request.model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job = db.query(ModelMirrorJob).filter(ModelMirrorJob.model_id == request.model_id).first()
    if job and job.status in ("pending", "running"):
        raise HTTPException(status_code=409, detail=f"Mirror job already in progress for model {request.model_id}")
    if job:
        job.status = "pending"
        job.error_message = None
    else:
        job = ModelMirrorJob(model_id=request.model_id, status="pending")
        db.add(job)
    db.commit()
    db.refresh(job)

    try:
        workflow_id = ModelDownloaderService().submit_download(model_id=request.model_id)
    except Exception as e:
        logger.exception(f"The mirror of {request.model_id} could not be submitted")
        job.status = "failed"
        job.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to submit mirror: {e}")

    job.workflow_name = workflow_id
    job.status = "running"
    db.commit()
    logger.info(f"Model mirror submitted: {request.model_id} -> job {job.id}, workflow {workflow_id}")
    return MirrorResponse(job_id=str(job.id), workflow_id=workflow_id, model_id=request.model_id,
                          status="running", message="Mirror job submitted successfully")


@router.get("/mirrors", response_model=MirrorJobsResponse, operation_id="models_list_mirrors")
def list_mirror_jobs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every mirror job, newest first: running, succeeded, failed and cancelled."""
    jobs = db.query(ModelMirrorJob).order_by(ModelMirrorJob.created_at.desc()).all()
    return MirrorJobsResponse(jobs=[_status(job) for job in jobs])


@router.get("/mirrors/active", operation_id="models_count_active_mirrors")
def count_active_mirrors(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """How many mirrors are pending or running: Control's top bar shows the count."""
    count = db.query(ModelMirrorJob).filter(ModelMirrorJob.status.in_(["pending", "running"])).count()
    return {"count": count}


def _status(job: ModelMirrorJob) -> MirrorStatus:
    """The job as the API answers it, with its workflow's page in Argo's web UI."""
    url = f"https://argo.{settings.DOMAIN_NAME}/workflows/argo/{job.workflow_name}" if job.workflow_name else None
    return MirrorStatus(**job.to_dict(), workflow_url=url)


def _job_by_workflow(db: Session, workflow_id: str) -> ModelMirrorJob:
    job = db.query(ModelMirrorJob).filter(ModelMirrorJob.workflow_name == workflow_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"No mirror job has the workflow {workflow_id}")
    return job


@router.get("/mirrors/{workflow_id}", response_model=MirrorStatus, operation_id="models_get_mirror")
def get_mirror_status(workflow_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A mirror job by its workflow_id, read again from its Argo workflow while it runs."""
    job = _job_by_workflow(db, workflow_id)
    if job.status in ("pending", "running"):
        ModelDownloaderService().sync_mirror_job(job)
        db.commit()
    return _status(job)


@router.delete("/mirrors/{workflow_id}", operation_id="models_cancel_mirror")
def cancel_model_mirror(workflow_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stop a running mirror: its Argo workflow is terminated and the job is cancelled."""
    job = _job_by_workflow(db, workflow_id)
    if not ModelDownloaderService().cancel_download(workflow_id):
        raise HTTPException(status_code=502, detail=f"Argo did not terminate the workflow {workflow_id}; see the backend's log")
    job.status = "cancelled"
    db.commit()
    return {"workflow_id": workflow_id, "status": "cancelled", "message": "Mirror cancelled"}


@router.post("/mirrors/reset/{model_id:path}", operation_id="models_reset_mirror")
def reset_mirror_job(model_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Forget a model's mirror job, so it can be mirrored again; MLflow is not touched."""
    job = db.query(ModelMirrorJob).filter(ModelMirrorJob.model_id == model_id).first()
    if not job:
        return {"model_id": model_id, "status": "no_job", "message": "No mirror job found to reset"}
    db.delete(job)
    db.commit()
    return {"model_id": model_id, "status": "reset", "message": "Mirror job reset; the model can be mirrored again"}


@router.delete("/registered/{model_id:path}", operation_id="models_delete")
async def delete_model(model_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete a model and all its versions from MLflow, then its mirror job, so it can be mirrored again."""
    try:
        await ModelDownloaderService().delete_model(model_id)
    except MlflowDeleteError as e:
        raise HTTPException(status_code=502, detail=str(e))
    job = db.query(ModelMirrorJob).filter(ModelMirrorJob.model_id == model_id).first()
    if job:
        db.delete(job)
        db.commit()
    return {"model_id": model_id, "status": "deleted", "message": f"Model {model_id} deleted"}


@router.get("/mlflow/status", operation_id="models_check_mlflow")
async def check_mlflow_status(current_user: User = Depends(get_current_user)):
    """Whether MLflow knows the platform's MLflow user. A mirror fails with unknown_user
    until the user has opened MLflow once in a browser, at mlflow_url."""
    mlflow_url = f"https://experiments.{settings.DOMAIN_NAME}"
    try:
        token = await mlflow_token()
    except MlflowDeleteError as e:
        return {"initialized": False, "needs_browser_login": True, "mlflow_url": mlflow_url, "error": str(e)}
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/experiments/search",
                               headers={"Authorization": f"Bearer {token}"}, params={"max_results": 10})
    except httpx.HTTPError as e:
        return {"initialized": False, "needs_browser_login": False, "mlflow_url": mlflow_url,
                "error": f"MLflow at {settings.MLFLOW_TRACKING_URI} did not answer: {e}"}
    if r.status_code == 200:
        found = len(r.json().get("experiments", []))
        return {"initialized": True, "needs_browser_login": False, "mlflow_url": mlflow_url,
                "message": f"MLflow is ready ({found} experiments found)"}
    if r.status_code in (401, 500):
        # MLflow answers 500 for a user its OIDC plugin has not created yet
        return {"initialized": False, "needs_browser_login": True, "mlflow_url": mlflow_url,
                "error": f"MLflow does not know the user yet (HTTP {r.status_code}): open {mlflow_url} once in a browser"}
    return {"initialized": False, "needs_browser_login": False, "mlflow_url": mlflow_url,
            "error": f"MLflow answered HTTP {r.status_code}: {r.text[:300]}"}


async def _control(token: str, method: str, path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=CONTROL_TIMEOUT) as http:
        return await http.request(method, f"{settings.CONTROL_API_URL}{path}",
                                  headers={"Authorization": f"Bearer {token}"})


@router.post("/finetuned", operation_id="models_register_finetuned")
async def register_finetuned_model(
    request: FinetunedModelRequest,
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """Publish a fine-tuned model whose merged 16-bit weights are already a model
    version in MLflow: write its catalogue entry to the user's metadata
    repository and record its weights as in place, so the LLM Gateway lists it,
    normally as deployable. The entry needs every catalogue field, with
    server_type ["vllm"] and quantization "BF16" (the merged weights) or
    "NVFP4" (their NVFP4 quantization, served on Blackwell GPUs only), or
    server_type ["llama-cpp"] and quantization "Q8_0" or "F16" (a GGUF file),
    is_finetuned true and serving_name equal to id. state is the model's state
    in the LLM Gateway after it read the catalogue again."""
    entry = request.catalog_entry
    try:
        finetuned_models.check_entry(entry)
        repository = await asyncio.to_thread(finetuned_models.write_entry, entry)
    except finetuned_models.FinetunedModelError as e:
        raise HTTPException(status_code=e.status, detail=str(e))

    job = db.query(ModelMirrorJob).filter(ModelMirrorJob.model_id == entry["id"]).first()
    if job is None:
        db.add(ModelMirrorJob(model_id=entry["id"], status="succeeded"))
    else:
        job.status = "succeeded"
        job.error_message = None
    db.commit()
    invalidate_catalogs()

    try:
        refreshed = await _control(token, "POST", "/llm/refresh/")
        if refreshed.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"The entry is in {repository}, but the LLM Gateway did not "
                                f"read the catalogue again: HTTP {refreshed.status_code}: {refreshed.text[:300]}")
        status = await _control(token, "GET", f"/llm/models/{entry['id']}/status")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"The entry is in {repository}, but Thinkube Control did not "
                            f"answer: {e}")
    state = status.json()["model"]["state"] if status.status_code == 200 else None
    return {"model_id": entry["id"], "repository": repository, "state": state}
