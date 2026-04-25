import logging

from fastapi import APIRouter

from app.api.llm.schemas import GPUStatusResponse
from app.services.llm_gpu_tracker import llm_gpu_tracker

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/",
    response_model=GPUStatusResponse,
    operation_id="get_llm_gpu_status",
)
async def get_gpu_status():
    return llm_gpu_tracker.get_status()
