# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

import logging

from fastapi import APIRouter

from app.api.llm.schemas import BackendsListResponse, BackendTypeEntry, BackendTypesListResponse
from app.services.llm_backend_discovery import llm_backend_discovery
from app.services.llm_backend_types import llm_backend_types

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/",
    response_model=BackendsListResponse,
    operation_id="get_llm_backends",
)
async def list_backends():
    """The running backend pods, with their health and the models they serve."""
    backends = llm_backend_discovery.list_backends()
    return BackendsListResponse(
        backends=backends,
        total=len(backends),
        healthy=sum(1 for b in backends if b.status == "healthy"),
    )


@router.get(
    "/types",
    response_model=BackendTypesListResponse,
    operation_id="get_llm_backend_types",
)
async def list_backend_types():
    """The installed backend types models can be loaded on.

    Each is a component that declares itself an LLM gateway backend
    (spec.llm_backend in its thinkube.yaml). A model's server_type names the
    types it runs on. driver per-model-pod: each model gets its own pod;
    driver ollama: one pod per node serves several models. tier is
    performance or flexible.
    """
    return BackendTypesListResponse(
        backend_types=[
            BackendTypeEntry(
                type=b.type, display_name=b.display_name, namespace=b.namespace,
                driver=b.driver, tier=b.tier,
            )
            for b in llm_backend_types.list()
        ]
    )
