import asyncio
import logging
import os
from typing import Optional

import httpx

from app.api.llm.schemas import ModelLoadResponse, ModelState, ModelTier, model_id_to_ollama_name

logger = logging.getLogger(__name__)

MLFLOW_ARTIFACT_BASE = "/mlflow-models/artifacts"


class LLMLifecycleManager:
    def __init__(self):
        self._load_timeout = int(os.getenv("LLM_MODEL_LOAD_TIMEOUT_SECONDS", "300"))
        self._loading_locks: dict[str, asyncio.Event] = {}

    LOADABLE_TYPES = {"ollama", "vllm", "tensorrt-llm"}

    async def load_model(
        self,
        model_id: str,
        tier: Optional[ModelTier] = None,
        keep_alive: Optional[str] = None,
        backend: Optional[str] = None,
        node: Optional[str] = None,
    ) -> ModelLoadResponse:
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker

        entry = llm_model_registry.get_model(model_id)
        if entry is None:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.registered, message=f"Model '{model_id}' not found"
            )

        if not any(st in self.LOADABLE_TYPES for st in entry.server_type):
            return ModelLoadResponse(
                model_id=model_id, state=entry.state,
                message=f"Model type {entry.server_type} cannot be loaded dynamically"
            )

        if entry.state == ModelState.available:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.available,
                message="Model is already loaded", backend_id=entry.backend_id
            )

        if entry.state == ModelState.loading:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.loading, message="Model is already loading"
            )

        resolved_backend = backend
        if resolved_backend and "-" in resolved_backend:
            parts = resolved_backend.split("-", 1)
            if not node:
                node = parts[1]
            resolved_backend = parts[0]

        if not resolved_backend:
            resolved_tier = tier or (
                ModelTier.performance if any(t in ("vllm", "tensorrt-llm") for t in entry.server_type)
                else ModelTier.flexible
            )
            if resolved_tier == ModelTier.flexible and "ollama" in entry.server_type:
                resolved_backend = "ollama"
            elif resolved_tier == ModelTier.performance:
                return await self._load_performance(model_id, backend=backend, node=node)
            elif "ollama" in entry.server_type:
                resolved_backend = "ollama"

        if resolved_backend == "ollama":
            return await self._load_ollama(model_id, keep_alive, node)

        if resolved_backend in ("vllm", "tensorrt-llm"):
            return await self._load_performance(model_id, backend=backend, node=node)

        return ModelLoadResponse(
            model_id=model_id, state=entry.state,
            message=f"No supported backend for model '{model_id}' with server_type={entry.server_type}"
        )

    async def _load_ollama(
        self, model_id: str, keep_alive: Optional[str] = None, node: Optional[str] = None
    ) -> ModelLoadResponse:
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker
        from app.services.llm_ollama_client import ollama_client

        if not await ollama_client.is_available(node):
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.deployable,
                message="Ollama backend is not available"
            )

        entry = llm_model_registry.get_model(model_id)
        estimated_memory = self._estimate_memory(entry)

        can_load, reason = llm_gpu_tracker.check_can_load(
            estimated_memory, node_name=node
        )
        if not can_load:
            candidate = self._select_eviction_candidate(target_node=node)
            if candidate:
                logger.info(f"Evicting {candidate} to make room for {model_id}")
                await self.unload_model(candidate)
                can_load, reason = llm_gpu_tracker.check_can_load(
                    estimated_memory, node_name=node
                )

            if not can_load:
                return ModelLoadResponse(
                    model_id=model_id, state=ModelState.deployable,
                    message=f"Insufficient GPU resources: {reason}"
                )

        if node:
            target_node = node
        elif can_load and reason not in ("ok",):
            target_node = reason
        else:
            target_node = None

        backend_id = f"ollama-{target_node}" if target_node else "ollama"
        llm_model_registry.update_model_state(model_id, ModelState.loading, backend_id)

        asyncio.create_task(
            self._load_ollama_background(model_id, keep_alive, target_node, backend_id, estimated_memory)
        )

        return ModelLoadResponse(
            model_id=model_id, state=ModelState.loading,
            message="Loading model — this may take several minutes", backend_id=backend_id
        )

    async def _load_ollama_background(
        self, model_id: str, keep_alive: Optional[str],
        target_node: Optional[str], backend_id: str, estimated_memory: float,
    ):
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker
        from app.services.llm_ollama_client import ollama_client

        event = asyncio.Event()
        self._loading_locks[model_id] = event

        try:
            entry = llm_model_registry.get_model(model_id)
            catalog_serving = entry.serving_name if entry else None

            models = await ollama_client.list_models(node=target_node)
            model_names = [m.get("name", "") for m in models]
            ollama_name = self._find_ollama_name(model_id, model_names)
            if not ollama_name and catalog_serving:
                ollama_name = self._find_ollama_name(catalog_serving, model_names)

            load_error = None
            if ollama_name:
                success, load_error = await ollama_client.load_model(ollama_name, keep_alive, node=target_node)
            else:
                gguf_path = await self._resolve_gguf_path(model_id)
                if not gguf_path:
                    llm_model_registry.update_model_state(
                        model_id, ModelState.deployable,
                        error=f"GGUF artifact not found for '{model_id}' in MLflow",
                    )
                    logger.error(f"Could not find GGUF artifact for '{model_id}' in MLflow")
                    return

                ollama_name = catalog_serving or model_id_to_ollama_name(model_id)
                logger.info(f"Creating Ollama model '{ollama_name}' from {gguf_path}")
                success = await ollama_client.create_model(ollama_name, gguf_path, node=target_node)
                if success:
                    llm_model_registry.register_ollama_alias(ollama_name, model_id)
                    success, load_error = await ollama_client.load_model(ollama_name, keep_alive, node=target_node)
                else:
                    success = False
                    load_error = f"Failed to create Ollama model from GGUF"

            if success:
                llm_model_registry.update_model_state(model_id, ModelState.available, backend_id)
                llm_gpu_tracker.record_allocation(
                    model_id, backend_id, estimated_memory, node_name=target_node
                )
                logger.info(f"Model '{model_id}' loaded successfully on {backend_id}")
            else:
                llm_model_registry.update_model_state(
                    model_id, ModelState.deployable, error=load_error,
                )
                logger.error(f"Failed to load model '{model_id}' on Ollama: {load_error}")
        except Exception as e:
            logger.error(f"Background load failed for {model_id}: {e}")
            llm_model_registry.update_model_state(
                model_id, ModelState.deployable, error=str(e),
            )
        finally:
            event.set()
            self._loading_locks.pop(model_id, None)

    async def _load_performance(
        self, model_id: str,
        backend: Optional[str] = None, node: Optional[str] = None,
    ) -> ModelLoadResponse:
        from app.services.llm_backend_discovery import llm_backend_discovery
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker

        entry = llm_model_registry.get_model(model_id)
        if entry is None:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.registered,
                message=f"Model '{model_id}' not found"
            )

        all_backends = llm_backend_discovery.list_backends()
        compatible = [
            b for b in all_backends
            if b.type in (entry.server_type or []) and b.status == "healthy"
        ]

        if backend:
            compatible = [b for b in compatible if b.id == backend]
        if node:
            compatible = [b for b in compatible if b.node == node]

        if not compatible:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.deployable,
                message="No compatible healthy backends available. Deploy a vLLM or TRT-LLM component first."
            )

        target_backend = compatible[0]

        estimated_memory = self._estimate_memory(entry)
        can_load, reason = llm_gpu_tracker.check_can_load(
            estimated_memory, node_name=target_backend.node
        )
        if not can_load:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.deployable,
                message=f"Insufficient GPU resources: {reason}"
            )

        payload: dict = {"model_id": model_id}
        if entry.stop_tokens:
            payload["stop_tokens"] = entry.stop_tokens
        if entry.reasoning_format:
            payload["reasoning_format"] = entry.reasoning_format
        if entry.tool_use:
            payload["tool_use"] = entry.tool_use

        llm_model_registry.update_model_state(model_id, ModelState.loading, target_backend.id)

        asyncio.create_task(
            self._load_performance_background(
                model_id, target_backend, payload, estimated_memory
            )
        )

        return ModelLoadResponse(
            model_id=model_id, state=ModelState.loading,
            message="Loading model on performance backend — this may take several minutes",
            backend_id=target_backend.id
        )

    async def _load_performance_background(
        self, model_id: str, backend, payload: dict, estimated_memory: float,
    ):
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker

        event = asyncio.Event()
        self._loading_locks[model_id] = event

        try:
            async with httpx.AsyncClient(
                verify=False, timeout=httpx.Timeout(660.0, connect=10.0)
            ) as client:
                resp = await client.post(
                    f"{backend.url}/admin/switch-model",
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()

            if result.get("status") == "serving" or result.get("current_model"):
                llm_model_registry.update_model_state(
                    model_id, ModelState.available, backend.id
                )
                llm_gpu_tracker.record_allocation(
                    model_id, backend.id, estimated_memory, node_name=backend.node
                )
                logger.info(f"Model '{model_id}' loaded on {backend.id}")
            else:
                error = result.get("error", "Backend returned non-serving status")
                llm_model_registry.update_model_state(
                    model_id, ModelState.deployable, error=error
                )
                logger.error(f"Failed to load '{model_id}' on {backend.id}: {error}")
        except Exception as e:
            logger.error(f"Performance load failed for {model_id}: {e}")
            llm_model_registry.update_model_state(
                model_id, ModelState.deployable, error=str(e)
            )
        finally:
            event.set()
            self._loading_locks.pop(model_id, None)

    async def unload_model(self, model_id: str, force: bool = False) -> ModelLoadResponse:
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker
        from app.services.llm_ollama_client import ollama_client

        entry = llm_model_registry.get_model(model_id)
        if entry is None:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.registered,
                message=f"Model '{model_id}' not found"
            )

        if entry.state != ModelState.available:
            return ModelLoadResponse(
                model_id=model_id, state=entry.state,
                message=f"Model is not loaded (state: {entry.state.value})"
            )

        if entry.backend_id and entry.backend_id.startswith("ollama"):
            node = None
            if "-" in entry.backend_id:
                node = entry.backend_id.split("-", 1)[1]

            llm_model_registry.update_model_state(model_id, ModelState.unloading, entry.backend_id)

            models = await ollama_client.list_models(node=node)
            model_names = [m.get("name", "") for m in models]
            ollama_name = self._find_ollama_name(model_id, model_names)
            if not ollama_name and entry.serving_name:
                ollama_name = self._find_ollama_name(entry.serving_name, model_names)
            if not ollama_name:
                ollama_name = entry.serving_name or model_id_to_ollama_name(model_id)

            success = await ollama_client.unload_model(ollama_name, node=node)
            if success:
                llm_model_registry.update_model_state(model_id, ModelState.deployable)
                llm_gpu_tracker.release_allocation(model_id)
                return ModelLoadResponse(
                    model_id=model_id, state=ModelState.deployable,
                    message="Model unloaded successfully"
                )
            else:
                llm_model_registry.update_model_state(model_id, ModelState.available, entry.backend_id)
                return ModelLoadResponse(
                    model_id=model_id, state=ModelState.available,
                    message="Failed to unload model from Ollama"
                )

        return ModelLoadResponse(
            model_id=model_id, state=entry.state,
            message="Unloading performance-tier models is not supported via API"
        )

    async def auto_load_on_resolve(self, model_id: str, timeout: Optional[int] = None) -> bool:
        if model_id in self._loading_locks:
            event = self._loading_locks[model_id]
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout or self._load_timeout)
            except asyncio.TimeoutError:
                return False
            from app.services.llm_model_registry import llm_model_registry
            entry = llm_model_registry.get_model(model_id)
            return entry is not None and entry.state == ModelState.available

        result = await self.load_model(model_id)
        return result.state == ModelState.available

    async def _resolve_gguf_path(self, model_id: str) -> Optional[str]:
        mlflow_url = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlflow.svc.cluster.local:5000")
        token = await self._get_mlflow_token()
        if not token:
            logger.error("Cannot get MLflow auth token")
            return None

        headers = {"Authorization": f"Bearer {token}"}
        model_name = model_id.replace("/", "-")

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                resp = await client.get(
                    f"{mlflow_url}/api/2.0/mlflow/model-versions/search",
                    params={"filter": f"name='{model_name}'"},
                    headers=headers,
                )
                resp.raise_for_status()
                versions = resp.json().get("model_versions", [])
                if not versions:
                    logger.warning(f"Model '{model_name}' not found in MLflow registry")
                    return None

                latest = max(versions, key=lambda v: int(v["version"]))
                run_id = latest["run_id"]

                resp = await client.get(
                    f"{mlflow_url}/api/2.0/mlflow/runs/get",
                    params={"run_id": run_id},
                    headers=headers,
                )
                resp.raise_for_status()
                experiment_id = resp.json()["run"]["info"]["experiment_id"]

            model_dir = f"{MLFLOW_ARTIFACT_BASE}/{experiment_id}/{run_id}/artifacts/model"

            gguf_file = await self._find_gguf_filename(run_id, token)
            if gguf_file:
                return f"{model_dir}/{gguf_file}"

            logger.warning(f"No GGUF file found for run {run_id}")
            return None

        except Exception as e:
            logger.error(f"Failed to resolve GGUF path for {model_id}: {e}")
            return None

    async def _find_gguf_filename(self, run_id: str, token: str) -> Optional[str]:
        mlflow_url = os.getenv("MLFLOW_TRACKING_URI")
        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                resp = await client.get(
                    f"{mlflow_url}/api/2.0/mlflow/artifacts/list",
                    params={"run_id": run_id, "path": "model"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    files = resp.json().get("files", [])
                    for f in files:
                        path = f.get("path", "")
                        if path.endswith(".gguf"):
                            return path.split("/")[-1]
        except Exception as e:
            logger.debug(f"MLflow artifact list failed: {e}")
        return None

    async def _get_mlflow_token(self) -> Optional[str]:
        token_url = os.getenv("MLFLOW_KEYCLOAK_TOKEN_URL")
        client_id = os.getenv("MLFLOW_KEYCLOAK_CLIENT_ID")
        client_secret = os.getenv("MLFLOW_CLIENT_SECRET")
        username = os.getenv("MLFLOW_AUTH_USERNAME")
        password = os.getenv("MLFLOW_AUTH_PASSWORD")

        if not all([token_url, client_id, client_secret, username, password]):
            logger.error("Missing MLflow auth credentials")
            return None

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "password",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "username": username,
                        "password": password,
                        "scope": "openid",
                    },
                )
                resp.raise_for_status()
                return resp.json()["access_token"]
        except Exception as e:
            logger.error(f"Failed to get MLflow token: {e}")
            return None

    def _find_ollama_name(self, model_id: str, available: list[str]) -> Optional[str]:
        for name in available:
            if name == model_id or name.startswith(f"{model_id}:"):
                return name
        short = model_id.split("/")[-1] if "/" in model_id else model_id
        for name in available:
            if name == short or name.startswith(f"{short}:"):
                return name
        return None

    def _estimate_memory(self, entry) -> float:
        if entry is None:
            return 4.0

        if getattr(entry, "params_b", None):
            return self._estimate_from_params(entry.params_b, entry.quantization)

        size = entry.size or ""
        quant = (entry.quantization or "").lower()

        size_gb = self._parse_size_gb(size)
        if not size_gb:
            return 4.0

        is_gguf = any(
            st in ("ollama",) for st in (entry.server_type or [])
        ) and ("q4" in quant or "q8" in quant or "gguf" in quant or "q5" in quant or "q6" in quant)

        if is_gguf:
            return size_gb

        if "q4" in quant or "4bit" in quant:
            return size_gb * 0.3
        if "q8" in quant or "8bit" in quant:
            return size_gb * 0.55
        if "fp16" in quant or "f16" in quant:
            return size_gb * 0.55
        return size_gb * 0.35

    def _estimate_from_params(self, params_b: float, quantization: Optional[str]) -> float:
        quant = (quantization or "BF16").upper()
        if "FP4" in quant or "NVFP4" in quant:
            bpp = 0.5
        elif "FP8" in quant:
            bpp = 1.0
        elif "BF16" in quant or "FP16" in quant or "F16" in quant:
            bpp = 2.0
        elif "Q4" in quant:
            bpp = 0.56
        elif "Q8" in quant:
            bpp = 1.0
        elif "1.58" in quant:
            bpp = 0.2
        else:
            bpp = 1.0
        weight_gb = params_b * bpp
        return round(weight_gb * 1.2, 1)

    def _parse_size_gb(self, size: str) -> Optional[float]:
        s = size.lower().replace(" ", "").lstrip("~")
        if not s:
            return None
        try:
            if s.endswith("gb"):
                return float(s[:-2])
            if s.endswith("mb"):
                return float(s[:-2]) / 1024.0
            if s.endswith("b"):
                return float(s[:-1])
            return float(s)
        except ValueError:
            return None

    def _select_eviction_candidate(self, target_node: Optional[str] = None) -> Optional[str]:
        from app.services.llm_gpu_tracker import llm_gpu_tracker

        candidates = llm_gpu_tracker.get_eviction_candidates()
        if target_node:
            flexible = [
                c for c in candidates
                if c.backend_id.startswith("ollama") and c.node_name == target_node
            ]
        else:
            flexible = [c for c in candidates if c.backend_id.startswith("ollama")]
        if flexible:
            return flexible[0].model_id
        return None


llm_lifecycle = LLMLifecycleManager()
