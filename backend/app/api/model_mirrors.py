# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
API endpoints for HuggingFace model mirroring to MLflow
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Any, List, Dict, Optional
import asyncio
import logging

from pydantic import BaseModel

from app.core.api_tokens import get_current_user_dual_auth
from app.services.model_downloader import ModelDownloaderService, MlflowDeleteError, catalog_entry
from app.db.session import get_db
from app.models.model_mirrors import ModelMirrorJob
from app.core.config import settings
from app.services.metadata_fetcher import CatalogUnavailableError, invalidate_catalogs
from app.services import finetuned_models
from app.services.llm_model_registry import llm_model_registry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["models"])


# Request/Response Models

class ModelInfo(BaseModel):
    """Model information"""
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
    """Response for model catalog"""
    models: List[ModelInfo]


class MirrorRequest(BaseModel):
    """Request to mirror a model"""
    model_id: str


class MirrorResponse(BaseModel):
    """Response after submitting a mirror job"""
    job_id: str
    workflow_id: str
    model_id: str
    status: str
    message: str


class MirrorStatus(BaseModel):
    """Mirror job status information"""
    id: str
    model_id: str
    status: str
    workflow_name: Optional[str]
    error_message: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    is_running: bool
    is_complete: bool
    is_failed: bool


class MirrorJobsResponse(BaseModel):
    """Response for mirror jobs list"""
    jobs: List[MirrorStatus]


# Helpers

def estimate_size_from_params(params_b: float, quantization: str) -> str:
    """Compute human-readable weight size from params_b and quantization."""
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
    gb = params_b * bpp
    return f"~{gb:.0f}GB"


# API Endpoints

@router.get("/catalog", response_model=ModelCatalogResponse, operation_id="get_model_catalog")
async def get_model_catalog(
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    The model catalogue: the platform's models.json merged with the user's,
    each with whether it is already mirrored
    """
    try:
        service = ModelDownloaderService()
        models = service.get_available_models()

        # Check which models are already downloaded
        downloaded_models = service.check_all_models_exist()

        # Enrich model info with download status and computed size
        model_infos = []
        for model in models:
            if not model.get("size") and model.get("params_b"):
                model = {**model, "size": estimate_size_from_params(
                    model["params_b"], model.get("quantization", "BF16")
                )}
            model_info = ModelInfo(
                **model,
                is_downloaded=downloaded_models.get(model["id"], False)
            )
            model_infos.append(model_info)

        return ModelCatalogResponse(models=model_infos)

    except CatalogUnavailableError:
        raise
    except Exception as e:
        logger.error(f"Failed to get model catalog: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get model catalog: {str(e)}"
        )


@router.post("/mirrors", response_model=MirrorResponse, operation_id="submit_model_mirror")
async def submit_model_mirror(
    request: MirrorRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Submit a model mirror job

    Creates a database record and submits an Argo Workflow to mirror the specified model
    from HuggingFace to MLflow. Returns immediately with job_id - mirroring happens in background.
    """
    try:
        # A model outside the catalog is refused before a job record exists,
        # so a refusal leaves nothing that blocks a later submission.
        catalog_entry(request.model_id)

        # Check for existing active mirror job for this model
        existing_job = db.query(ModelMirrorJob).filter(
            ModelMirrorJob.model_id == request.model_id
        ).first()

        if existing_job and existing_job.status in ['pending', 'running']:
            raise HTTPException(
                status_code=409,
                detail=f"Mirror job already in progress for model {request.model_id}"
            )

        # Create or update job record
        if existing_job:
            job = existing_job
            job.status = "pending"
            job.error_message = None
        else:
            job = ModelMirrorJob(model_id=request.model_id, status="pending")
            db.add(job)

        db.commit()
        db.refresh(job)

        # Submit workflow
        service = ModelDownloaderService()
        workflow_id = service.submit_download(model_id=request.model_id)

        # Update job with workflow info
        job.workflow_name = workflow_id
        job.status = "running"
        db.commit()

        logger.info(f"Model mirror submitted: {request.model_id} -> job {job.id}, workflow {workflow_id}")

        return MirrorResponse(
            job_id=str(job.id),
            workflow_id=workflow_id,
            model_id=request.model_id,
            status="running",
            message=f"Mirror job submitted successfully"
        )

    except HTTPException:
        raise

    except ValueError as e:
        # Model not in catalog
        logger.warning(f"Invalid model requested: {request.model_id}")
        raise HTTPException(status_code=400, detail=str(e))

    except CatalogUnavailableError:
        raise
    except Exception as e:
        logger.error(f"Failed to submit mirror for {request.model_id}: {e}")
        # Mark job as failed if it was created
        if 'job' in locals():
            job.status = "failed"
            job.error_message = str(e)
            db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to submit mirror: {str(e)}"
        )


@router.get("/mirrors", response_model=MirrorJobsResponse, operation_id="list_mirror_jobs")
async def list_mirror_jobs(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    List all model mirror jobs

    Returns all mirror jobs from database (active, completed, and failed)
    """
    try:
        # Query all jobs, ordered by most recent first
        jobs = db.query(ModelMirrorJob).order_by(
            ModelMirrorJob.created_at.desc()
        ).all()

        # Sync active jobs with Argo workflows
        service = ModelDownloaderService()
        for job in jobs:
            if job.status in ['pending', 'running'] and job.workflow_name:
                try:
                    service.sync_mirror_job(job)
                except Exception as e:
                    logger.warning(f"Could not sync job {job.id} with workflow: {e}")

        db.commit()

        job_statuses = [MirrorStatus(**job.to_dict()) for job in jobs]

        return MirrorJobsResponse(jobs=job_statuses)

    except Exception as e:
        logger.error(f"Failed to list mirror jobs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list mirror jobs: {str(e)}"
        )


@router.get("/mirrors/{workflow_id}", response_model=MirrorStatus, operation_id="get_mirror_status")
async def get_mirror_status(
    workflow_id: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Get status of a specific mirror job by workflow_id

    Syncs with Argo and updates database
    """
    try:
        # Find job by workflow_name
        job = db.query(ModelMirrorJob).filter(
            ModelMirrorJob.workflow_name == workflow_id
        ).first()

        if not job:
            raise HTTPException(status_code=404, detail="Mirror job not found")

        # Sync with Argo if active
        if job.status in ['pending', 'running']:
            service = ModelDownloaderService()
            try:
                workflow_status = service.get_download_status(workflow_id)
                if workflow_status["status"] == "Succeeded":
                    job.status = "succeeded"
                    job.error_message = None
                elif workflow_status["status"] in ["Failed", "Error"]:
                    job.status = "failed"
                    job.error_message = workflow_status.get("message", "Workflow failed")
                db.commit()
            except Exception as e:
                logger.warning(f"Could not sync with workflow: {e}")

        return MirrorStatus(**job.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get status for {workflow_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get mirror status: {str(e)}"
        )


@router.delete("/mirrors/{workflow_id}", operation_id="cancel_model_mirror")
async def cancel_model_mirror(
    workflow_id: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Cancel a running model mirror

    Terminates the Argo Workflow and updates job status
    """
    try:
        # Find job
        job = db.query(ModelMirrorJob).filter(
            ModelMirrorJob.workflow_name == workflow_id
        ).first()

        # Cancel workflow
        service = ModelDownloaderService()
        success = service.cancel_download(workflow_id)

        if success:
            # Update job status
            if job:
                job.status = "cancelled"
                db.commit()

            return {
                "workflow_id": workflow_id,
                "status": "cancelled",
                "message": "Mirror cancelled successfully"
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to cancel mirror"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel mirror {workflow_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel mirror: {str(e)}"
        )


@router.get("/mlflow/status", operation_id="check_mlflow_status")
async def check_mlflow_status(
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    Check if MLflow is initialized for the current user

    Tests MLflow authentication by attempting to connect and list experiments.
    Returns initialization status and MLflow URL for browser login if needed.
    """
    import os
    import requests
    import base64

    # Get MLflow configuration
    mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlflow.svc.cluster.local:5000")
    mlflow_public_url = f"https://experiments.{settings.DOMAIN_NAME}"

    try:
        # Get credentials from secret
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except:
                k8s_config.load_kube_config()

            v1 = k8s_client.CoreV1Api()
            secret = v1.read_namespaced_secret("mlflow-auth-config", "thinkube-control")
            keycloak_token_url = base64.b64decode(secret.data['keycloak-token-url']).decode('utf-8')
            client_id = base64.b64decode(secret.data['client-id']).decode('utf-8')
            client_secret = base64.b64decode(secret.data['client-secret']).decode('utf-8')
            username = base64.b64decode(secret.data['username']).decode('utf-8')
            password = base64.b64decode(secret.data['password']).decode('utf-8')
        except Exception as e:
            logger.error(f"Could not read MLflow auth config secret: {e}")
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": "MLflow authentication not configured"
            }

        # Try to get OAuth2 token from Keycloak
        token_response = requests.post(
            keycloak_token_url,
            data={
                'grant_type': 'password',
                'client_id': client_id,
                'client_secret': client_secret,
                'username': username,
                'password': password
            },
            timeout=5
        )

        if token_response.status_code != 200:
            logger.error(f"Keycloak token request failed: status={token_response.status_code}, response={token_response.text[:500]}")
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": "Could not authenticate with Keycloak"
            }

        access_token = token_response.json().get('access_token')
        if not access_token:
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": "No access token received"
            }

        # Test MLflow connection with direct HTTP request (more reliable than Python client)
        mlflow_response = requests.get(
            f"{mlflow_uri}/api/2.0/mlflow/experiments/search",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"max_results": 10},
            timeout=5
        )

        if mlflow_response.status_code == 200:
            experiments = mlflow_response.json().get('experiments', [])
            result = {
                "initialized": True,
                "needs_browser_login": False,
                "mlflow_url": mlflow_public_url,
                "message": f"MLflow is ready ({len(experiments)} experiments found)"
            }
            logger.info(f"MLflow status check succeeded: {result}")
            return result
        elif mlflow_response.status_code == 500:
            # 500 error usually means user not initialized in MLflow OIDC
            logger.info(f"MLflow returned 500 - user likely not initialized")
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": "MLflow user not initialized - please log in via browser to create your account"
            }
        elif mlflow_response.status_code == 401:
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": "MLflow authentication failed - please log in via browser"
            }
        else:
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": f"MLflow returned status {mlflow_response.status_code}"
            }

    except requests.exceptions.Timeout:
        return {
            "initialized": False,
            "needs_browser_login": True,
            "mlflow_url": mlflow_public_url,
            "error": "MLflow connection timeout - service may be unavailable"
        }

    except Exception as e:
        logger.error(f"MLflow status check failed: {e}", exc_info=True)
        return {
            "initialized": False,
            "needs_browser_login": True,
            "mlflow_url": mlflow_public_url,
            "error": str(e)
        }


@router.post("/mirrors/reset/{model_id:path}", operation_id="reset_mirror_job")
async def reset_mirror_job(
    model_id: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Reset a mirror job by deleting the job record

    This clears the mirror job from the database without touching the model in MLflow.
    Useful when a job gets stuck or fails and you want to retry without deleting the model.
    """
    try:
        job = db.query(ModelMirrorJob).filter(
            ModelMirrorJob.model_id == model_id
        ).first()

        if job:
            db.delete(job)
            db.commit()
            logger.info(f"Reset mirror job for {model_id}")

            return {
                "model_id": model_id,
                "status": "reset",
                "message": f"Mirror job reset successfully - you can now retry"
            }
        else:
            return {
                "model_id": model_id,
                "status": "no_job",
                "message": "No mirror job found to reset"
            }

    except Exception as e:
        logger.error(f"Failed to reset mirror job for {model_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reset mirror job: {str(e)}"
        )


@router.delete("/models/{model_id:path}", operation_id="delete_model")
async def delete_model(
    model_id: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Delete a model from MLflow registry

    This removes the model and all its versions from MLflow, allowing it to be re-downloaded.
    Useful for cleaning up failed or incomplete downloads.
    """
    service = ModelDownloaderService()
    try:
        await service.delete_model(model_id)
    except MlflowDeleteError as e:
        logger.error(f"Failed to delete model {model_id}: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    # The mirror job record goes only once MLflow no longer has the model.
    job = db.query(ModelMirrorJob).filter(
        ModelMirrorJob.model_id == model_id
    ).first()
    if job:
        db.delete(job)
        db.commit()
        logger.info(f"Deleted mirror job for {model_id}")

    return {
        "model_id": model_id,
        "status": "deleted",
        "message": f"Model {model_id} deleted successfully"
    }


class FinetunedModelRequest(BaseModel):
    catalog_entry: Dict[str, Any]


@router.post("/finetuned", operation_id="register_finetuned_model")
async def register_finetuned_model(
    request: FinetunedModelRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db),
):
    """Publish a fine-tuned model whose merged 16-bit weights are already a model
    version in MLflow: write its catalogue entry to the user's metadata
    repository and record its weights as in place, so the LLM Gateway lists it,
    normally as deployable. The entry needs every catalogue field, with
    server_type ["vllm"] and quantization "BF16" (the merged weights) or
    "NVFP4" (their NVFP4 quantization, served on Blackwell GPUs only), or
    server_type ["llama-cpp"] and quantization "Q8_0" or "F16" (a GGUF file),
    is_finetuned true and serving_name equal to id."""
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
    await llm_model_registry.refresh()
    listed = llm_model_registry.get_model(entry["id"])
    return {
        "model_id": entry["id"],
        "repository": repository,
        "state": listed.state if listed else None,
    }
