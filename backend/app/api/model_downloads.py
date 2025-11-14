"""
API endpoints for HuggingFace model downloads
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Optional
import logging

from pydantic import BaseModel

from app.core.api_tokens import get_current_user_dual_auth
from app.services.model_downloader import ModelDownloaderService

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


class DownloadRequest(BaseModel):
    """Request to download a model"""
    model_id: str
    hf_token: Optional[str] = None


class DownloadResponse(BaseModel):
    """Response after submitting a download"""
    workflow_id: str
    model_id: str
    status: str
    message: str


class DownloadStatus(BaseModel):
    """Download status information"""
    workflow_name: str
    model_id: Optional[str]
    status: str
    started_at: Optional[str]
    finished_at: Optional[str]
    message: str
    is_running: bool
    is_complete: bool
    is_failed: bool


class ActiveDownloadsResponse(BaseModel):
    """Response for active downloads list"""
    downloads: List[DownloadStatus]


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


@router.post("/download", response_model=DownloadResponse, operation_id="submit_model_download")
async def submit_model_download(
    request: DownloadRequest,
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    Submit a model download workflow

    Submits an Argo Workflow to download the specified model to thinkube-models PVC.
    Returns immediately with workflow ID - download happens in background.
    """
    try:
        service = ModelDownloaderService()

        # Submit workflow
        workflow_id = service.submit_download(
            model_id=request.model_id,
            hf_token=request.hf_token
        )

        logger.info(f"Model download submitted: {request.model_id} -> {workflow_id}")

        return DownloadResponse(
            workflow_id=workflow_id,
            model_id=request.model_id,
            status="submitted",
            message=f"Download workflow submitted successfully. Workflow ID: {workflow_id}"
        )

    except ValueError as e:
        # Model not in catalog
        logger.warning(f"Invalid model requested: {request.model_id}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Failed to submit download for {request.model_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to submit download: {str(e)}"
        )


@router.get("/downloads", response_model=ActiveDownloadsResponse, operation_id="list_active_downloads")
async def list_active_downloads(
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    List all active (running or pending) model downloads

    Returns workflows with status Pending or Running
    """
    try:
        service = ModelDownloaderService()
        downloads = service.list_active_downloads()

        download_statuses = [DownloadStatus(**dl) for dl in downloads]

        return ActiveDownloadsResponse(downloads=download_statuses)

    except Exception as e:
        logger.error(f"Failed to list active downloads: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list active downloads: {str(e)}"
        )


@router.get("/downloads/{workflow_id}", response_model=DownloadStatus, operation_id="get_download_status")
async def get_download_status(
    workflow_id: str,
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    Get status of a specific download workflow

    Returns detailed status information for the workflow
    """
    try:
        service = ModelDownloaderService()
        status = service.get_download_status(workflow_id)

        return DownloadStatus(**status)

    except Exception as e:
        logger.error(f"Failed to get status for {workflow_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get download status: {str(e)}"
        )


@router.delete("/downloads/{workflow_id}", operation_id="cancel_model_download")
async def cancel_model_download(
    workflow_id: str,
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """
    Cancel a running model download

    Terminates the Argo Workflow
    """
    try:
        service = ModelDownloaderService()
        success = service.cancel_download(workflow_id)

        if success:
            return {
                "workflow_id": workflow_id,
                "status": "cancelled",
                "message": "Download cancelled successfully"
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to cancel download"
            )

    except Exception as e:
        logger.error(f"Failed to cancel download {workflow_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel download: {str(e)}"
        )
