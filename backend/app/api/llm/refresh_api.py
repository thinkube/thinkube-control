import logging

from fastapi import APIRouter

from app.api.llm.schemas import RefreshResponse
from app.services.llm_model_registry import llm_model_registry
from app.services.llm_backend_discovery import llm_backend_discovery

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
    return RefreshResponse(
        models_refreshed=models_count,
        backends_refreshed=backends_count,
    )
