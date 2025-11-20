"""
API endpoints for HuggingFace model mirroring to MLflow
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
import logging

from pydantic import BaseModel

from app.core.api_tokens import get_current_user_dual_auth
from app.services.model_downloader import ModelDownloaderService
from app.db.session import get_db
from app.models.model_mirrors import ModelMirrorJob

logger = logging.getLogger(__name__)
router = APIRouter(tags=["models"])


# Request/Response Models

class ModelInfo(BaseModel):
    """Model information"""
    id: str
    name: str
    size: str
    quantization: str
    description: str
    server_type: List[str]
    is_downloaded: bool = False


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


# API Endpoints

@router.get("/catalog", response_model=ModelCatalogResponse, operation_id="get_model_catalog")
async def get_model_catalog(
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    Get catalog of available models for download

    Returns list of 20 models optimized for TensorRT-LLM, including whether each is already downloaded
    """
    try:
        service = ModelDownloaderService()
        models = service.get_available_models()

        # Check which models are already downloaded
        downloaded_models = service.check_all_models_exist()

        # Enrich model info with download status
        model_infos = []
        for model in models:
            model_info = ModelInfo(
                **model,
                is_downloaded=downloaded_models.get(model["id"], False)
            )
            model_infos.append(model_info)

        return ModelCatalogResponse(models=model_infos)

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
                    workflow_status = service.get_download_status(job.workflow_name)
                    # Update job if workflow status changed
                    if workflow_status["status"] == "Succeeded" and job.status != "succeeded":
                        job.status = "succeeded"
                        job.error_message = None
                    elif workflow_status["status"] in ["Failed", "Error"] and job.status != "failed":
                        job.status = "failed"
                        job.error_message = workflow_status.get("message", "Workflow failed")
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
    import mlflow

    try:
        # Get MLflow configuration
        mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlflow.svc.cluster.local:5000")
        mlflow_public_url = f"https://mlflow.{os.getenv('DOMAIN_NAME', 'thinkube.com')}"

        # Get credentials from secret
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except:
                k8s_config.load_kube_config()

            v1 = k8s_client.CoreV1Api()
            secret = v1.read_namespaced_secret("mlflow-auth-config", "thinkube-control")
            import base64
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
            verify=False,
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

        # Set MLflow token
        access_token = token_response.json().get('access_token')
        if not access_token:
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": "No access token received"
            }

        # Try to connect to MLflow
        os.environ['MLFLOW_TRACKING_TOKEN'] = access_token
        mlflow.set_tracking_uri(mlflow_uri)

        # Test connection by listing experiments with timeout
        import asyncio
        import concurrent.futures

        def check_mlflow_connection():
            return mlflow.search_experiments()

        try:
            # Run MLflow API call with timeout in thread pool
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                experiments = await asyncio.wait_for(
                    loop.run_in_executor(pool, check_mlflow_connection),
                    timeout=10.0
                )
        except asyncio.TimeoutError:
            return {
                "initialized": False,
                "needs_browser_login": True,
                "mlflow_url": mlflow_public_url,
                "error": "MLflow connection timeout - service may be unavailable"
            }

        result = {
            "initialized": True,
            "needs_browser_login": False,
            "mlflow_url": mlflow_public_url,
            "message": f"MLflow is ready ({len(experiments)} experiments found)"
        }
        logger.info(f"MLflow status check succeeded: {result}")
        return result

    except requests.exceptions.Timeout:
        # Timeout usually means user needs to initialize via browser
        return {
            "initialized": False,
            "needs_browser_login": True,
            "mlflow_url": mlflow_public_url,
            "error": "MLflow user not initialized - please log in via browser first"
        }

    except Exception as e:
        logger.error(f"MLflow status check failed: {e}", exc_info=True)
        result = {
            "initialized": False,
            "needs_browser_login": True,
            "mlflow_url": mlflow_public_url,
            "error": str(e)
        }
        logger.info(f"Returning error response: {result}")
        return result


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
    try:
        service = ModelDownloaderService()
        success = service.delete_model(model_id)

        if success:
            # Also clean up any associated mirror jobs
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
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete model {model_id}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete model {model_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete model: {str(e)}"
        )
