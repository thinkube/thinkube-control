# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

import logging

from fastapi import APIRouter

from app.api.llm.schemas import RefreshResponse
from app.services.llm_model_registry import llm_model_registry
from app.services.llm_backend_discovery import llm_backend_discovery
from app.services.llm_gpu_tracker import llm_gpu_tracker
from app.services.metadata_fetcher import invalidate_catalogs

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/",
    response_model=RefreshResponse,
    operation_id="refresh_llm_registry",
)
async def refresh_registry():
    """Read the model catalog, the backends and the GPU nodes again, now.

    The catalog is read from its repositories, not from a cached copy, so an
    entry pushed to a metadata repository is seen at once.
    """
    invalidate_catalogs()
    models_count = await llm_model_registry.refresh()
    backends_count = await llm_backend_discovery.refresh()
    nodes_count = llm_gpu_tracker.refresh_nodes()
    return RefreshResponse(
        models_refreshed=models_count,
        backends_refreshed=backends_count,
        message=f"{nodes_count} GPU node(s)",
    )
