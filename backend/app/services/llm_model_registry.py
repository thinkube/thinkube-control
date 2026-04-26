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
    model_id_to_ollama_name,
)

logger = logging.getLogger(__name__)


class LLMModelRegistry:
    def __init__(self):
        self._models: Dict[str, ModelEntry] = {}
        self._ollama_aliases: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._refresh_interval = int(
            os.getenv("LLM_MODEL_REFRESH_INTERVAL_SECONDS", "60")
        )
        self._is_running = False

    def list_models(self) -> List[ModelEntry]:
        return list(self._models.values())

    def get_model(self, model_id: str) -> Optional[ModelEntry]:
        return self._models.get(model_id)

    def register_ollama_alias(self, ollama_name: str, registry_id: str):
        base = ollama_name.split(":")[0]
        self._ollama_aliases[base] = registry_id
        self._ollama_aliases[ollama_name] = registry_id

    def resolve_ollama_alias(self, ollama_name: str) -> Optional[str]:
        base = ollama_name.split(":")[0]
        return self._ollama_aliases.get(base) or self._ollama_aliases.get(ollama_name)

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

        if entry.backend_id and entry.state == ModelState.available:
            backend = llm_backend_discovery.get_backend(entry.backend_id)
            if backend and backend.status == "healthy":
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

        registry_id = self.resolve_ollama_alias(alias)
        if registry_id and registry_id in self._models:
            return self._models[registry_id]

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

        for model_id, existing in self._models.items():
            if model_id not in updated and existing.state in (ModelState.available, ModelState.loading):
                updated[model_id] = existing

        self._models = updated

        for model_id, model in updated.items():
            if "ollama" in model.server_type:
                ollama_name = model_id_to_ollama_name(model_id)
                self.register_ollama_alias(ollama_name, model_id)

        logger.debug(f"Model registry refreshed: {len(updated)} models mirrored to MLflow")
        return len(updated)

    def update_model_state(
        self, model_id: str, state: ModelState, backend_id: Optional[str] = None,
        error: Optional[str] = None,
    ):
        if model_id in self._models:
            self._models[model_id].state = state
            if backend_id is not None:
                self._models[model_id].backend_id = backend_id
            self._models[model_id].last_error = error

    async def start_polling(self):
        if self._is_running:
            return
        self._is_running = True
        logger.info(
            f"Starting LLM model registry polling (interval={self._refresh_interval}s)"
        )

        self._poll_catalog()

        await asyncio.sleep(2)
        self._reconcile_states()

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
        from app.services.llm_gpu_tracker import llm_gpu_tracker
        from app.services.llm_lifecycle import llm_lifecycle

        backend_models: dict[str, str] = {}
        for backend in llm_backend_discovery.list_backends():
            if backend.status == "healthy":
                for model_name in backend.models:
                    if model_name not in backend_models:
                        backend_models[model_name] = backend.id

        matched_serving = set()

        for model_id, entry in self._models.items():
            serving_name = None
            if model_id in backend_models:
                serving_name = model_id
            else:
                ollama_name = model_id_to_ollama_name(model_id)
                for bm_name in backend_models:
                    bm_base = bm_name.split(":")[0]
                    if bm_base == ollama_name:
                        serving_name = bm_name
                        break

            if serving_name and entry.state != ModelState.loading:
                entry.state = ModelState.available
                entry.backend_id = backend_models[serving_name]
                matched_serving.add(serving_name)
                self._sync_gpu_allocation(
                    model_id, backend_models[serving_name],
                    llm_gpu_tracker, llm_lifecycle,
                )
            elif (
                serving_name is None
                and entry.state == ModelState.available
            ):
                entry.state = ModelState.deployable
                entry.backend_id = None
                llm_gpu_tracker.release_allocation(model_id)

        for model_name, backend_id in backend_models.items():
            if model_name in matched_serving:
                continue
            registry_id = self.resolve_ollama_alias(model_name)
            if registry_id and registry_id in self._models:
                continue
            if model_name in self._models:
                continue
            self._models[model_name] = ModelEntry(
                id=model_name,
                name=model_name,
                server_type=["ollama"] if backend_id.startswith("ollama") else [],
                state=ModelState.available,
                backend_id=backend_id,
                tier=ModelTier.flexible if backend_id.startswith("ollama") else ModelTier.performance,
            )

    def _sync_gpu_allocation(self, model_id, backend_id, gpu_tracker, lifecycle):
        existing = gpu_tracker.get_eviction_candidates()
        if any(a.model_id == model_id for a in existing):
            return
        entry = self._models.get(model_id)
        estimated = lifecycle._estimate_memory(entry) if entry else 4.0
        node_name = None
        if backend_id and "-" in backend_id:
            node_name = backend_id.split("-", 1)[1]
        gpu_tracker.record_allocation(model_id, backend_id, estimated, node_name=node_name)


llm_model_registry = LLMModelRegistry()
