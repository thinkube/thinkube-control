import logging

from fastapi import APIRouter

from app.api.llm.schemas import BackendsListResponse
from app.services.llm_backend_discovery import llm_backend_discovery

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/",
    response_model=BackendsListResponse,
    operation_id="get_llm_backends",
)
async def list_backends():
    backends = llm_backend_discovery.list_backends()
    return BackendsListResponse(
        backends=backends,
        total=len(backends),
        healthy=sum(1 for b in backends if b.status == "healthy"),
    )
