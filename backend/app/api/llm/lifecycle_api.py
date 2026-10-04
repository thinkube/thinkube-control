# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

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
    """What a load of this model can use: the GPU nodes and the memory it needs.

    gpu_nodes is every GPU node with its ai_remaining_gb, available_slots and
    the models allocated on it; a node fits when ai_remaining_gb covers
    estimated_memory_gb (the estimate is for the default context, the largest
    of 32768, 16384 and 8192 the model allows) and a slot is free.

    compatible_backends lists only the backend pods running now whose type the
    model supports. It is not the list of nodes that can serve the model: a
    vLLM, TensorRT-LLM or text-embeddings pod is created on the chosen node by
    the load itself, so a node without a pod is a valid target. Each such pod
    serves one model; a node whose allocations already hold a backend of the
    same type refuses a second model until the first is unloaded.
    """
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
                node=b.node,
            ))

    gpu_status = await llm_gpu_tracker.get_status()
    gpu_nodes = gpu_status.nodes

    # The estimate is for the largest context a load defaults to; the load
    # itself steps down when the chosen node has less memory.
    default_context = min(llm_lifecycle.CONTEXT_CHOICES[0], entry.context_length or llm_lifecycle.CONTEXT_CHOICES[0])
    estimated_memory = llm_lifecycle._estimate_memory(entry, default_context)

    return LoadOptionsResponse(
        model_id=model_id,
        compatible_backends=backends,
        gpu_nodes=gpu_nodes,
        estimated_memory_gb=estimated_memory,
        context_length=entry.context_length,
    )


@router.post(
    "/{model_id:path}/load",
    response_model=ModelLoadResponse,
    operation_id="load_llm_model",
)
async def load_model(model_id: str, request: ModelLoadRequest = ModelLoadRequest()):
    """Load a mirrored model on a GPU node; answers at once, the load runs on.

    node names the target; without it the node with the most AI memory left
    takes the model. backend is a type (vllm, tensorrt-llm, text-embeddings,
    ollama) or a discovered backend id such as vllm-tkspark, which also names
    the node. max_context_length defaults to the largest of 32768, 16384 and
    8192 that the model allows and the node has memory for.

    The answer is HTTP 200 whether the load was accepted or refused: state
    "loading" with a backend_id means accepted, any other state means refused
    and message says why (already loaded, node taken by another model of the
    same one-model backend, does not fit the node, backend disabled). Follow
    the load with get_llm_model_status until the state is "available", or
    "deployable" with last_error when it failed.
    """
    entry = llm_model_registry.get_model(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    result = await llm_lifecycle.load_model(
        model_id,
        tier=request.tier,
        keep_alive=request.keep_alive,
        backend=request.backend,
        node=request.node,
        max_context_length=request.max_context_length,
        num_speculative_tokens=request.num_speculative_tokens,
    )
    return result


@router.post(
    "/{model_id:path}/unload",
    response_model=ModelLoadResponse,
    operation_id="unload_llm_model",
)
async def unload_model(model_id: str, request: ModelUnloadRequest = ModelUnloadRequest()):
    """Unload a served model and free its GPU memory.

    Only a model in state "available" can be unloaded; the answer's message
    names the state otherwise. The last model on a vLLM, TensorRT-LLM or
    text-embeddings pod takes the pod down with it. Other users may be calling
    the model: unload only what you loaded or what the user asked to stop.
    """
    entry = llm_model_registry.get_model(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    result = await llm_lifecycle.unload_model(model_id, request.force)
    return result
