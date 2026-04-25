import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self):
        self._base_url = os.getenv(
            "LLM_OLLAMA_URL", "http://ollama.ollama.svc.cluster.local:11434"
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=30.0, verify=False
        )
        self._load_timeout = int(os.getenv("LLM_MODEL_LOAD_TIMEOUT_SECONDS", "300"))

    async def is_available(self) -> bool:
        try:
            resp = await self._client.get("/")
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        resp = await self._client.get("/api/tags")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def list_running(self) -> List[Dict[str, Any]]:
        resp = await self._client.get("/api/ps")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def pull_model(self, name: str) -> bool:
        try:
            async with self._client.stream(
                "POST",
                "/api/pull",
                json={"name": name, "stream": True},
                timeout=httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.strip():
                        logger.debug(f"Ollama pull progress ({name}): {line[:200]}")
            return True
        except Exception as e:
            logger.error(f"Ollama pull failed for {name}: {e}")
            return False

    async def create_model(self, name: str, modelfile: str) -> bool:
        try:
            resp = await self._client.post(
                "/api/create",
                json={"name": name, "modelfile": modelfile},
                timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0),
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Ollama create failed for {name}: {e}")
            return False

    async def load_model(self, name: str, keep_alive: Optional[str] = None) -> bool:
        payload: Dict[str, Any] = {"model": name, "prompt": ""}
        if keep_alive:
            payload["keep_alive"] = keep_alive

        try:
            resp = await self._client.post(
                "/api/generate",
                json=payload,
                timeout=httpx.Timeout(
                    connect=30.0, read=float(self._load_timeout), write=30.0, pool=30.0
                ),
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Ollama load failed for {name}: {e}")
            return False

    async def unload_model(self, name: str) -> bool:
        try:
            resp = await self._client.post(
                "/api/generate",
                json={"model": name, "keep_alive": 0},
                timeout=60.0,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Ollama unload failed for {name}: {e}")
            return False

    async def is_model_loaded(self, name: str) -> bool:
        running = await self.list_running()
        return any(m.get("name", "").startswith(name) for m in running)

    async def wait_for_model(self, name: str, timeout: Optional[int] = None) -> bool:
        timeout = timeout or self._load_timeout
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self.is_model_loaded(name):
                return True
            await asyncio.sleep(2)
        return False

    async def close(self):
        await self._client.aclose()


ollama_client = OllamaClient()
