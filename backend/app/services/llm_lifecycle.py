import asyncio
import logging
import os
from typing import Optional

import httpx

from app.api.llm.schemas import ModelLoadResponse, ModelState, ModelTier

logger = logging.getLogger(__name__)

MLFLOW_ARTIFACT_BASE = "/mlflow-models/artifacts"


class LLMLifecycleManager:
    def __init__(self):
        self._load_timeout = int(os.getenv("LLM_MODEL_LOAD_TIMEOUT_SECONDS", "300"))
        self._loading_locks: dict[str, asyncio.Event] = {}

    async def load_model(
        self, model_id: str, tier: Optional[ModelTier] = None, keep_alive: Optional[str] = None
    ) -> ModelLoadResponse:
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker

        entry = llm_model_registry.get_model(model_id)
        if entry is None:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.registered, message=f"Model '{model_id}' not found"
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

        resolved_tier = tier or (
            ModelTier.performance if any(t in ("vllm", "tensorrt-llm") for t in entry.server_type)
            else ModelTier.flexible
        )

        if resolved_tier == ModelTier.flexible and "ollama" in entry.server_type:
            return await self._load_ollama(model_id, keep_alive)

        if resolved_tier == ModelTier.performance:
            return await self._load_performance(model_id)

        if "ollama" in entry.server_type:
            return await self._load_ollama(model_id, keep_alive)

        return ModelLoadResponse(
            model_id=model_id, state=entry.state,
            message=f"No supported backend for model '{model_id}' with server_type={entry.server_type}"
        )

    async def _load_ollama(self, model_id: str, keep_alive: Optional[str] = None) -> ModelLoadResponse:
        from app.services.llm_model_registry import llm_model_registry
        from app.services.llm_gpu_tracker import llm_gpu_tracker
        from app.services.llm_ollama_client import ollama_client

        if not await ollama_client.is_available():
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.deployable,
                message="Ollama backend is not available"
            )

        entry = llm_model_registry.get_model(model_id)
        estimated_memory = self._estimate_memory(entry)

        can_load, reason = llm_gpu_tracker.check_can_load(estimated_memory)
        if not can_load:
            candidate = self._select_eviction_candidate()
            if candidate:
                logger.info(f"Evicting {candidate} to make room for {model_id}")
                await self.unload_model(candidate)
                can_load, reason = llm_gpu_tracker.check_can_load(estimated_memory)

            if not can_load:
                return ModelLoadResponse(
                    model_id=model_id, state=ModelState.deployable,
                    message=f"Insufficient GPU resources: {reason}"
                )

        llm_model_registry.update_model_state(model_id, ModelState.loading, "ollama")
        event = asyncio.Event()
        self._loading_locks[model_id] = event

        try:
            models = await ollama_client.list_models()
            model_names = [m.get("name", "") for m in models]
            ollama_name = self._find_ollama_name(model_id, model_names)

            if ollama_name:
                success = await ollama_client.load_model(ollama_name, keep_alive)
            else:
                gguf_path = await self._resolve_gguf_path(model_id)
                if not gguf_path:
                    llm_model_registry.update_model_state(model_id, ModelState.deployable)
                    return ModelLoadResponse(
                        model_id=model_id, state=ModelState.deployable,
                        message=f"Could not find GGUF artifact for '{model_id}' in MLflow"
                    )

                ollama_name = self._model_id_to_ollama_name(model_id)
                logger.info(f"Creating Ollama model '{ollama_name}' from {gguf_path}")
                success = await ollama_client.create_model(ollama_name, gguf_path)
                if success:
                    success = await ollama_client.load_model(ollama_name, keep_alive)

            if success:
                llm_model_registry.update_model_state(model_id, ModelState.available, "ollama")
                llm_gpu_tracker.record_allocation(model_id, "ollama", estimated_memory)
                return ModelLoadResponse(
                    model_id=model_id, state=ModelState.available,
                    message="Model loaded successfully", backend_id="ollama"
                )
            else:
                llm_model_registry.update_model_state(model_id, ModelState.deployable)
                return ModelLoadResponse(
                    model_id=model_id, state=ModelState.deployable,
                    message="Failed to load model on Ollama"
                )
        except Exception as e:
            logger.error(f"Load failed for {model_id}: {e}")
            llm_model_registry.update_model_state(model_id, ModelState.deployable)
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.deployable,
                message=f"Load failed: {e}"
            )
        finally:
            event.set()
            self._loading_locks.pop(model_id, None)

    async def _load_performance(self, model_id: str) -> ModelLoadResponse:
        from app.services.llm_backend_discovery import llm_backend_discovery

        backends = llm_backend_discovery.list_backends()
        perf_backends = [
            b for b in backends
            if b.type in ("vllm", "tensorrt-llm") and b.status == "healthy"
        ]

        if not perf_backends:
            return ModelLoadResponse(
                model_id=model_id, state=ModelState.deployable,
                message="No performance-tier backends available. Deploy a vLLM or TRT-LLM template first."
            )

        return ModelLoadResponse(
            model_id=model_id, state=ModelState.deployable,
            message="Performance-tier model loading requires deploying via template. Use the thinkube-control UI."
        )

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

        if entry.backend_id == "ollama":
            llm_model_registry.update_model_state(model_id, ModelState.unloading, "ollama")
            models = await ollama_client.list_models()
            model_names = [m.get("name", "") for m in models]
            ollama_name = self._find_ollama_name(model_id, model_names)
            if not ollama_name:
                ollama_name = self._model_id_to_ollama_name(model_id)
            success = await ollama_client.delete_model(ollama_name)
            if success:
                llm_model_registry.update_model_state(model_id, ModelState.deployable)
                llm_gpu_tracker.release_allocation(model_id)
                return ModelLoadResponse(
                    model_id=model_id, state=ModelState.deployable,
                    message="Model unloaded successfully"
                )
            else:
                llm_model_registry.update_model_state(model_id, ModelState.available, "ollama")
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

    def _model_id_to_ollama_name(self, model_id: str) -> str:
        name = model_id.split("/")[-1].lower()
        for suffix in ["-gguf", "-ggml"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name

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

        size = entry.size or ""
        quant = (entry.quantization or "").lower()

        size_gb = self._parse_size_gb(size)
        if size_gb:
            if "q4" in quant or "4bit" in quant:
                return size_gb * 0.3
            if "q8" in quant or "8bit" in quant:
                return size_gb * 0.55
            if "fp16" in quant or "f16" in quant:
                return size_gb * 0.55
            return size_gb * 0.35

        return 4.0

    def _parse_size_gb(self, size: str) -> Optional[float]:
        s = size.lower().replace(" ", "")
        if not s:
            return None
        try:
            if s.endswith("b"):
                num = float(s[:-1])
                return num
            return float(s)
        except ValueError:
            return None

    def _select_eviction_candidate(self) -> Optional[str]:
        from app.services.llm_gpu_tracker import llm_gpu_tracker

        candidates = llm_gpu_tracker.get_eviction_candidates()
        flexible_candidates = [c for c in candidates if c.backend_id == "ollama"]
        if flexible_candidates:
            return flexible_candidates[0].model_id
        return None


llm_lifecycle = LLMLifecycleManager()
