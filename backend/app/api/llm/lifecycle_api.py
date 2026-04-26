import logging

from fastapi import APIRouter, HTTPException

from app.api.llm.schemas import (
    LoadOptionBackend,
    LoadOptionsResponse,
    ModelLoadRequest,
    ModelLoadResponse,
    ModelUnloadRequest,
)
from app.services.llm_lifecycle import llm_lifecycle
from app.services.llm_model_registry import llm_model_registry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/{model_id:path}/load-options",
    response_model=LoadOptionsResponse,
    operation_id="get_llm_load_options",
)
async def get_load_options(model_id: str):
    from app.services.llm_backend_discovery import llm_backend_discovery
    from app.services.llm_gpu_tracker import llm_gpu_tracker

    entry = llm_model_registry.get_model(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    backends = []
    for b in llm_backend_discovery.list_backends():
        if b.type in (entry.server_type or []):
            backends.append(LoadOptionBackend(
                id=b.id,
                name=b.name,
                type=b.type,
                status=b.status,
            ))

    gpu_status = llm_gpu_tracker.get_status()
    estimated_memory = llm_lifecycle._estimate_memory(entry)

    return LoadOptionsResponse(
        model_id=model_id,
        compatible_backends=backends,
        gpu_nodes=gpu_status.nodes,
        estimated_memory_gb=estimated_memory,
    )


@router.post(
    "/{model_id:path}/load",
    response_model=ModelLoadResponse,
    operation_id="load_llm_model",
)
async def load_model(model_id: str, request: ModelLoadRequest = ModelLoadRequest()):
    entry = llm_model_registry.get_model(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    result = await llm_lifecycle.load_model(
        model_id,
        tier=request.tier,
        keep_alive=request.keep_alive,
        backend=request.backend,
        node=request.node,
    )
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
