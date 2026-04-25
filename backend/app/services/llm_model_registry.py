"""
LLM Model Registry

Maintains an in-memory cache of models from the platform catalog and MLflow.
Cross-references with backend discovery to determine model states.
Provides the fast resolve endpoint used by the Go proxy on every request.
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional

from app.api.llm.schemas import (
    ModelEntry,
    ModelResolveResponse,
    ModelState,
    ModelTier,
)

logger = logging.getLogger(__name__)


class LLMModelRegistry:
    def __init__(self):
        self._models: Dict[str, ModelEntry] = {}
        self._lock = asyncio.Lock()
        self._refresh_interval = int(
            os.getenv("LLM_MODEL_REFRESH_INTERVAL_SECONDS", "60")
        )
        self._is_running = False

    def list_models(self) -> List[ModelEntry]:
        return list(self._models.values())

    def get_model(self, model_id: str) -> Optional[ModelEntry]:
        return self._models.get(model_id)

    def get_backends_for_model(self, model_id: str) -> List[str]:
        from app.services.llm_backend_discovery import llm_backend_discovery

        return [
            b.id for b in llm_backend_discovery.list_backends() if model_id in b.models
        ]

    def resolve(
        self, alias: str, tier: Optional[str] = None
    ) -> Optional[ModelResolveResponse]:
        from app.services.llm_backend_discovery import llm_backend_discovery

        entry = self._resolve_alias(alias)
        if entry is None:
            return None

        if entry.state not in (ModelState.available, ModelState.deployable):
            return ModelResolveResponse(
                backend_url="",
                model_id=entry.id,
                model_state=entry.state,
                tier=entry.tier or ModelTier.flexible,
                error=f"Model '{entry.id}' is {entry.state.value}",
            )

        backends = llm_backend_discovery.get_backends_serving(entry.id)

        if tier == "performance":
            backends = [b for b in backends if b.type in ("vllm", "tensorrt-llm")] or backends
        elif tier == "flexible":
            backends = [b for b in backends if b.type == "ollama"] or backends
        else:
            perf = [b for b in backends if b.type in ("vllm", "tensorrt-llm")]
            if perf:
                backends = perf

        if not backends:
            if entry.state == ModelState.deployable:
                return ModelResolveResponse(
                    backend_url="",
                    model_id=entry.id,
                    model_state=entry.state,
                    tier=ModelTier.flexible
                    if "ollama" in entry.server_type
                    else ModelTier.performance,
                )
            return ModelResolveResponse(
                backend_url="",
                model_id=entry.id,
                model_state=entry.state,
                tier=entry.tier or ModelTier.flexible,
                error=f"No healthy backend serving model '{entry.id}'",
            )

        backend = backends[0]
        resolved_tier = (
            ModelTier.performance
            if backend.type in ("vllm", "tensorrt-llm")
            else ModelTier.flexible
        )

        return ModelResolveResponse(
            backend_url=backend.url,
            api_path=backend.api_path,
            model_id=entry.id,
            model_state=ModelState.available,
            tier=resolved_tier,
        )

    def _resolve_alias(self, alias: str) -> Optional[ModelEntry]:
        if alias in self._models:
            return self._models[alias]

        matches = [m for m in self._models.values() if m.name.lower() == alias.lower()]
        if len(matches) == 1:
            return matches[0]

        for m in self._models.values():
            if alias.lower() in m.id.lower().split("/")[-1].lower():
                return m

        return None

    async def refresh(self) -> int:
        async with self._lock:
            return self._poll_catalog()

    def _poll_catalog(self) -> int:
        from app.services.model_downloader import get_model_catalog, ModelDownloaderService

        catalog = get_model_catalog()

        try:
            downloader = ModelDownloaderService()
            mirrored = downloader.check_all_models_exist()
        except Exception as e:
            logger.warning(f"Could not check MLflow mirror status: {e}")
            mirrored = {}

        catalog_by_id = {entry["id"]: entry for entry in catalog}
        updated = {}

        for model_id, is_mirrored in mirrored.items():
            if not is_mirrored:
                continue

            entry = catalog_by_id.get(model_id, {})
            existing = self._models.get(model_id)

            if existing and existing.state in (ModelState.available, ModelState.loading, ModelState.unloading):
                initial_state = existing.state
            else:
                initial_state = ModelState.deployable

            model = ModelEntry(
                id=model_id,
                name=entry.get("name", model_id),
                server_type=entry.get("server_type", []),
                task=entry.get("task", "text-generation"),
                quantization=entry.get("quantization"),
                size=entry.get("size"),
                description=entry.get("description"),
                context_window=entry.get("context_window"),
                capabilities=entry.get("capabilities", []),
                is_finetuned=entry.get("is_finetuned", False),
                state=initial_state,
                backend_id=existing.backend_id if existing else None,
                tier=existing.tier if existing else None,
            )
            updated[model_id] = model

        # Keep any models that are currently loaded but not in catalog (e.g. fine-tuned)
        for model_id, existing in self._models.items():
            if model_id not in updated and existing.state in (ModelState.available, ModelState.loading):
                updated[model_id] = existing

        self._models = updated
        logger.debug(f"Model registry refreshed: {len(updated)} models mirrored to MLflow")
        return len(updated)

    def update_model_state(
        self, model_id: str, state: ModelState, backend_id: Optional[str] = None
    ):
        if model_id in self._models:
            self._models[model_id].state = state
            if backend_id is not None:
                self._models[model_id].backend_id = backend_id

    async def start_polling(self):
        if self._is_running:
            return
        self._is_running = True
        logger.info(
            f"Starting LLM model registry polling (interval={self._refresh_interval}s)"
        )

        self._poll_catalog()

        while self._is_running:
            try:
                await asyncio.sleep(self._refresh_interval)
                if self._is_running:
                    self._poll_catalog()
                    self._reconcile_states()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Model registry poll failed: {e}")

    def stop(self):
        self._is_running = False

    def _reconcile_states(self):
        from app.services.llm_backend_discovery import llm_backend_discovery

        serving_models = set()
        for backend in llm_backend_discovery.list_backends():
            if backend.status == "healthy":
                serving_models.update(backend.models)

        for model_id, entry in self._models.items():
            if model_id in serving_models and entry.state != ModelState.loading:
                entry.state = ModelState.available
            elif (
                model_id not in serving_models
                and entry.state == ModelState.available
            ):
                entry.state = ModelState.deployable


llm_model_registry = LLMModelRegistry()
