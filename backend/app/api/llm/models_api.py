import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.api.llm.schemas import (
    ModelState,
    ModelsListResponse,
    ModelResolveResponse,
    ModelStatusResponse,
)
from app.services.llm_model_registry import llm_model_registry

logger = logging.getLogger(__name__)
router = APIRouter()

BACKEND_TYPE_NAMESPACES = {
    "ollama": "ollama",
    "vllm": "vllm",
    "tensorrt-llm": "tensorrt",
}

_k8s_core_v1 = None


def _get_k8s_client():
    global _k8s_core_v1
    if _k8s_core_v1 is None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        _k8s_core_v1 = client.CoreV1Api()
    return _k8s_core_v1


def _get_installed_backend_types() -> list[str]:
    try:
        v1 = _get_k8s_client()
        installed = []
        for backend_type, namespace in BACKEND_TYPE_NAMESPACES.items():
            try:
                v1.read_namespaced_config_map(
                    name="thinkube-service-config", namespace=namespace
                )
                installed.append(backend_type)
            except ApiException:
                pass
        return installed
    except Exception as e:
        logger.warning(f"Could not check installed backend types: {e}")
        return []


@router.get(
    "/",
    response_model=ModelsListResponse,
    operation_id="get_llm_models",
)
async def list_models(
    state: Optional[ModelState] = Query(None, description="Filter by model state"),
    server_type: Optional[str] = Query(None, description="Filter by server type"),
):
    models = llm_model_registry.list_models()

    if state:
        models = [m for m in models if m.state == state]
    if server_type:
        models = [m for m in models if server_type in m.server_type]

    installed = _get_installed_backend_types()

    return ModelsListResponse(
        models=models,
        total=len(models),
        available=sum(1 for m in models if m.state == ModelState.available),
        deployable=sum(1 for m in models if m.state == ModelState.deployable),
        registered=sum(1 for m in models if m.state == ModelState.registered),
        installed_backend_types=installed,
    )


@router.get(
    "/resolve",
    response_model=ModelResolveResponse,
    operation_id="resolve_llm_model",
)
async def resolve_model(
    model: str = Query(..., description="Model alias or ID"),
    tier: Optional[str] = Query(None, description="Preferred tier: flexible or performance"),
):
    result = llm_model_registry.resolve(model, tier)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Model '{model}' not found")

    if result.model_state == ModelState.deployable and not result.error:
        entry = llm_model_registry.get_model(result.model_id)
        if entry and "ollama" in entry.server_type:
            from app.services.llm_lifecycle import llm_lifecycle

            loaded = await llm_lifecycle.auto_load_on_resolve(result.model_id, timeout=120)
            if loaded:
                result = llm_model_registry.resolve(model, tier)
                if result is None:
                    raise HTTPException(status_code=404, detail=f"Model '{model}' not found")

    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    return result


@router.get(
    "/{model_id:path}/status",
    response_model=ModelStatusResponse,
    operation_id="get_llm_model_status",
)
async def get_model_status(model_id: str):
    entry = llm_model_registry.get_model(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    backends = llm_model_registry.get_backends_for_model(model_id)
    return ModelStatusResponse(model=entry, backends=backends)
