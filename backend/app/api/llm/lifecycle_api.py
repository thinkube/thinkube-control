import logging

from fastapi import APIRouter, HTTPException

from app.api.llm.schemas import ModelLoadRequest, ModelLoadResponse, ModelUnloadRequest
from app.services.llm_lifecycle import llm_lifecycle
from app.services.llm_model_registry import llm_model_registry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/{model_id:path}/load",
    response_model=ModelLoadResponse,
    operation_id="load_llm_model",
)
async def load_model(model_id: str, request: ModelLoadRequest = ModelLoadRequest()):
    entry = llm_model_registry.get_model(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    result = await llm_lifecycle.load_model(model_id, request.tier, request.keep_alive)
    return result


@router.post(
    "/{model_id:path}/unload",
    response_model=ModelLoadResponse,
    operation_id="unload_llm_model",
)
async def unload_model(model_id: str, request: ModelUnloadRequest = ModelUnloadRequest()):
    entry = llm_model_registry.get_model(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    result = await llm_lifecycle.unload_model(model_id, request.force)
    return result
