import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_k8s_executor = ThreadPoolExecutor(max_workers=2)


class OllamaClient:
    """Routes requests to per-node Ollama instances discovered by backend discovery."""

    def __init__(self):
        self._default_url = os.getenv(
            "LLM_OLLAMA_URL", "http://ollama.ollama.svc.cluster.local:11434"
        )
        self._node_urls: Dict[str, str] = {}
        self._node_pods: Dict[str, str] = {}
        self._clients: Dict[str, httpx.AsyncClient] = {}
        self._load_timeout = int(os.getenv("LLM_MODEL_LOAD_TIMEOUT_SECONDS", "300"))

    def register_node(self, node_name: str, pod_ip: str, pod_name: str):
        url = f"http://{pod_ip}:11434"
        old_url = self._node_urls.get(node_name)
        self._node_urls[node_name] = url
        self._node_pods[node_name] = pod_name
        if old_url != url and node_name in self._clients:
            old_client = self._clients.pop(node_name)
            try:
                asyncio.get_event_loop().create_task(old_client.aclose())
            except RuntimeError:
                pass
        if node_name not in self._clients:
            self._clients[node_name] = httpx.AsyncClient(
                base_url=url, timeout=30.0, verify=False
            )
        logger.debug(f"Ollama node registered: {node_name} @ {url} (pod={pod_name})")

    def unregister_node(self, node_name: str):
        self._node_urls.pop(node_name, None)
        self._node_pods.pop(node_name, None)
        client = self._clients.pop(node_name, None)
        if client:
            try:
                asyncio.get_event_loop().create_task(client.aclose())
            except RuntimeError:
                pass

    def list_nodes(self) -> List[str]:
        return list(self._node_urls.keys())

    def _get_client(self, node: Optional[str] = None) -> httpx.AsyncClient:
        if node and node in self._clients:
            return self._clients[node]
        if self._clients:
            return next(iter(self._clients.values()))
        if "_default" not in self._clients:
            self._clients["_default"] = httpx.AsyncClient(
                base_url=self._default_url, timeout=30.0, verify=False
            )
        return self._clients["_default"]

    async def is_available(self, node: Optional[str] = None) -> bool:
        try:
            client = self._get_client(node)
            resp = await client.get("/")
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self, node: Optional[str] = None) -> List[Dict[str, Any]]:
        client = self._get_client(node)
        resp = await client.get("/api/tags")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def list_running(self, node: Optional[str] = None) -> List[Dict[str, Any]]:
        client = self._get_client(node)
        resp = await client.get("/api/ps")
        resp.raise_for_status()
        return resp.json().get("models", [])

    async def pull_model(self, name: str, node: Optional[str] = None) -> bool:
        try:
            client = self._get_client(node)
            async with client.stream(
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

    async def create_model(self, name: str, gguf_path: str, node: Optional[str] = None) -> bool:
        loop = asyncio.get_event_loop()
        pod_name = self._resolve_pod_name(node)
        return await loop.run_in_executor(
            _k8s_executor, self._create_model_via_exec, name, gguf_path, pod_name
        )

    def _resolve_pod_name(self, node: Optional[str] = None) -> str:
        if node and node in self._node_pods:
            return self._node_pods[node]
        if self._node_pods:
            return next(iter(self._node_pods.values()))
        return os.getenv("LLM_OLLAMA_POD", "ollama-0")

    def _create_model_via_exec(self, name: str, gguf_path: str, pod_name: str) -> bool:
        try:
            from kubernetes import client, config
            from kubernetes.stream import stream

            config.load_incluster_config()
            v1 = client.CoreV1Api()

            namespace = os.getenv("LLM_OLLAMA_NAMESPACE", "ollama")
            cmd = [
                "sh", "-c",
                f'printf "FROM {gguf_path}" > /tmp/Modelfile && '
                f"ollama create {name} -f /tmp/Modelfile && "
                f"rm -f /tmp/Modelfile",
            ]

            stream(
                v1.connect_get_namespaced_pod_exec,
                name=pod_name,
                namespace=namespace,
                command=cmd,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _request_timeout=300,
            )
            logger.info(f"Ollama create '{name}' on pod {pod_name} completed")
            return True
        except Exception as e:
            logger.error(f"Ollama create failed for {name} on {pod_name}: {e}")
            return False

    async def load_model(
        self, name: str, keep_alive: Optional[str] = None, node: Optional[str] = None
    ) -> tuple[bool, Optional[str]]:
        payload: Dict[str, Any] = {"model": name, "prompt": ""}
        payload["keep_alive"] = keep_alive if keep_alive else -1

        try:
            client = self._get_client(node)
            resp = await client.post(
                "/api/generate",
                json=payload,
                timeout=httpx.Timeout(
                    connect=30.0, read=float(self._load_timeout), write=30.0, pool=30.0
                ),
            )
            resp.raise_for_status()
            return True, None
        except httpx.HTTPStatusError as e:
            body = e.response.text[:200] if e.response else ""
            reason = f"Ollama returned {e.response.status_code}: {body}"
            logger.error(f"Ollama load failed for {name}: {reason}")
            return False, reason
        except Exception as e:
            reason = str(e)
            logger.error(f"Ollama load failed for {name}: {reason}")
            return False, reason

    async def unload_model(self, name: str, node: Optional[str] = None) -> bool:
        """Unload model from VRAM only (keeps model on disk for shared storage)."""
        try:
            client = self._get_client(node)
            resp = await client.post(
                "/api/generate",
                json={"model": name, "prompt": "", "keep_alive": "0s"},
                timeout=httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0),
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Ollama unload failed for {name}: {e}")
            return False

    async def delete_model(self, name: str, node: Optional[str] = None) -> bool:
        """Delete model completely from disk."""
        try:
            client = self._get_client(node)
            resp = await client.request(
                "DELETE",
                "/api/delete",
                json={"model": name},
                timeout=60.0,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Ollama delete failed for {name}: {e}")
            return False

    async def is_model_loaded(self, name: str, node: Optional[str] = None) -> bool:
        running = await self.list_running(node)
        return any(m.get("name", "").startswith(name) for m in running)

    async def wait_for_model(
        self, name: str, timeout: Optional[int] = None, node: Optional[str] = None
    ) -> bool:
        timeout = timeout or self._load_timeout
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self.is_model_loaded(name, node):
                return True
            await asyncio.sleep(2)
        return False

    async def close(self):
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        self._node_urls.clear()
        self._node_pods.clear()


ollama_client = OllamaClient()
