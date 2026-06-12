import logging

from fastapi import APIRouter

from app.api.llm.schemas import RefreshResponse
from app.services.llm_model_registry import llm_model_registry
from app.services.llm_backend_discovery import llm_backend_discovery
from app.services.llm_gpu_tracker import llm_gpu_tracker

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/",
    response_model=RefreshResponse,
    operation_id="refresh_llm_registry",
)
async def refresh_registry():
    models_count = await llm_model_registry.refresh()
    backends_count = await llm_backend_discovery.refresh()
    nodes_count = llm_gpu_tracker.refresh_nodes()
    return RefreshResponse(
        models_refreshed=models_count,
        backends_refreshed=backends_count,
        message=f"{nodes_count} GPU node(s)",
    )
