# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
LLM Backend Discovery

Discovers the gateway-managed pods (created by llm_pod_manager) of every
installed backend type (llm_backend_types) and periodically probes their
health and served models.
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional

import httpx

from app.api.llm.schemas import BackendEntry
from app.services.llm_backend_types import DRIVER_OLLAMA, llm_backend_types

logger = logging.getLogger(__name__)


class LLMBackendDiscovery:
    # Number of consecutive probe failures before marking a backend unhealthy.
    # Prevents transient network hiccups from clearing the models list and
    # causing intermittent 400 errors from the resolve endpoint.
    PROBE_FAILURE_THRESHOLD = 2

    def __init__(self):
        self._backends: Dict[str, BackendEntry] = {}
        self._probe_failures: Dict[str, int] = {}
        self._probe_interval = int(
            os.getenv("LLM_BACKEND_PROBE_INTERVAL_SECONDS", "30")
        )
        self._is_running = False
        self._client = httpx.AsyncClient(timeout=10.0, verify=False)

    def list_backends(self) -> List[BackendEntry]:
        return list(self._backends.values())

    def get_backend(self, backend_id: str) -> Optional[BackendEntry]:
        return self._backends.get(backend_id)

    def get_backends_serving(self, model_id: str) -> List[BackendEntry]:
        return [
            b
            for b in self._backends.values()
            if b.status == "healthy" and model_id in b.models
        ]

    async def refresh(self) -> int:
        await self._discover_all()
        return len(self._backends)

    async def _discover_all(self):
        llm_backend_types.refresh()
        await self._discover_gateway_pods()
        await self._probe_all()

    async def _discover_gateway_pods(self):
        """Discover pods created by the LLM Gateway pod manager.

        Scans the namespace of every backend type for Running pods with the
        gateway-managed label. Registers Ollama pods with the Ollama client for
        per-node routing.
        """
        from app.services.llm_ollama_client import ollama_client
        from app.services.llm_pod_manager import GATEWAY_LABEL, GATEWAY_LABEL_VALUE, MODEL_LABEL

        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            discovered = set()

            for backend_type in llm_backend_types.list():
                try:
                    pods = v1.list_namespaced_pod(
                        backend_type.namespace,
                        label_selector=f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}",
                    )
                except Exception:
                    continue

                for pod in pods.items:
                    if pod.status.phase != "Running" or not pod.status.pod_ip:
                        continue
                    node_name = pod.spec.node_name
                    if not node_name:
                        continue

                    pod_ip = pod.status.pod_ip
                    pod_name = pod.metadata.name
                    # A one-model backend's pod carries its model; several of
                    # them can share a node, so the model is part of the id.
                    slug = (pod.metadata.labels or {}).get(MODEL_LABEL)
                    prefix = f"{backend_type.type}-{node_name}"
                    backend_id = f"{prefix}/{slug}" if slug else prefix
                    where = f"{node_name}, {slug}" if slug else node_name
                    discovered.add(backend_id)

                    url = f"http://{pod_ip}:{backend_type.port}"
                    display = f"{backend_type.display_name} ({where})"
                    if backend_type.driver == DRIVER_OLLAMA:
                        ollama_client.register_node(node_name, url, pod_name, backend_type.namespace)

                    # The same pod keeps its entry, with the status and models
                    # its probes found; rebuilding it on every pass would show
                    # it as unknown and empty until the next probe. A new pod,
                    # or a pod at a new address, starts from unknown.
                    existing = self._backends.get(backend_id)
                    if existing is not None and existing.url == url:
                        existing.name = display
                        existing.node = node_name
                        continue
                    self._backends[backend_id] = BackendEntry(
                        id=backend_id,
                        name=display,
                        url=url,
                        type=backend_type.type,
                        api_path=backend_type.api_path,
                        status="unknown",
                        node=node_name,
                    )
                    self._probe_failures.pop(backend_id, None)

            stale = [bid for bid in list(self._backends) if bid not in discovered]
            for bid in stale:
                backend = self._backends[bid]
                described = llm_backend_types.get(backend.type)
                if described is not None and described.driver == DRIVER_OLLAMA and backend.node:
                    ollama_client.unregister_node(backend.node)
                del self._backends[bid]

        except Exception as e:
            logger.warning(f"Gateway pod discovery failed: {e}")

    async def _probe_all(self):
        tasks = [self._probe_backend(b) for b in list(self._backends.values())]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe_backend(self, backend: BackendEntry):
        from datetime import datetime
        try:
            backend_type = llm_backend_types.require(backend.type)
            if backend_type.driver == DRIVER_OLLAMA:
                await self._probe_ollama(backend)
            else:
                await self._probe_openai_compatible(backend, backend_type.health_path)
            # Probe succeeded — reset failure counter
            self._probe_failures[backend.id] = 0
        except Exception as e:
            failures = self._probe_failures.get(backend.id, 0) + 1
            self._probe_failures[backend.id] = failures
            if failures >= self.PROBE_FAILURE_THRESHOLD:
                backend.status = "unhealthy"
                backend.models = []
                logger.warning(f"Backend {backend.id} unhealthy after {failures} consecutive probe failures: {e}")
            else:
                logger.debug(f"Backend {backend.id} probe failed ({failures}/{self.PROBE_FAILURE_THRESHOLD}): {e}")
        finally:
            backend.last_probe = datetime.utcnow().isoformat()

    async def _probe_ollama(self, backend: BackendEntry):
        resp = await self._client.get(f"{backend.url}/api/tags")
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama /api/tags returned {resp.status_code}")

        # Use /api/ps to get models loaded in VRAM (not just on disk).
        # With shared JuiceFS storage, /api/tags returns the same models on all
        # nodes. /api/ps reflects what is actually loaded per node.
        ps_resp = await self._client.get(f"{backend.url}/api/ps")
        running = []
        if ps_resp.status_code == 200:
            for m in ps_resp.json().get("models", []):
                name = m.get("name", "")
                if name:
                    running.append(name)

        backend.status = "healthy"
        backend.models = running

    async def _probe_openai_compatible(self, backend: BackendEntry, health_path: str):
        health_url = f"{backend.url}{health_path}"
        resp = await self._client.get(health_url)
        if resp.status_code != 200:
            raise RuntimeError(f"Health check returned {resp.status_code}")

        models_url = f"{backend.url}{backend.api_path}/models"
        try:
            resp = await self._client.get(models_url)
            if resp.status_code == 200:
                data = resp.json()
                model_ids = [
                    m.get("id", "") for m in data.get("data", []) if m.get("id")
                ]
                backend.models = model_ids
            # Don't clear models on /v1/models failure — keep last known good list
        except Exception:
            pass  # Keep existing models list on models endpoint failure

        backend.status = "healthy"

    async def start_polling(self):
        if self._is_running:
            return
        self._is_running = True
        logger.info(
            f"Starting LLM backend discovery polling (interval={self._probe_interval}s)"
        )

        await self._discover_all()

        while self._is_running:
            try:
                await asyncio.sleep(self._probe_interval)
                if self._is_running:
                    await self._discover_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Backend discovery poll failed: {e}")

    def stop(self):
        self._is_running = False

    async def close(self):
        self.stop()
        await self._client.aclose()


llm_backend_discovery = LLMBackendDiscovery()
