"""
LLM Backend Discovery

Discovers model-serving backends via Kubernetes pods and ConfigMaps.
Periodically probes backend health and served models.
Discovers gateway-managed pods (created by llm_pod_manager) for all backend types.
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional

import httpx
import yaml

from app.api.llm.schemas import BackendEntry

logger = logging.getLogger(__name__)


class LLMBackendDiscovery:
    def __init__(self):
        self._backends: Dict[str, BackendEntry] = {}
        self._probe_interval = int(
            os.getenv("LLM_BACKEND_PROBE_INTERVAL_SECONDS", "30")
        )
        self._ollama_url = os.getenv(
            "LLM_OLLAMA_URL", "http://ollama.ollama.svc.cluster.local:11434"
        )
        self._static_backends_raw = os.getenv("LLM_STATIC_BACKENDS", "")
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
        self._discover_static()
        await self._discover_gateway_pods()
        await self._discover_from_configmaps()
        await self._probe_all()

    def _discover_static(self):
        if not self._static_backends_raw:
            return
        try:
            entries = yaml.safe_load(self._static_backends_raw)
            if not isinstance(entries, list):
                return
            for entry in entries:
                bid = entry.get("name", entry.get("url", "unknown"))
                self._backends[bid] = BackendEntry(
                    id=bid,
                    name=entry.get("name", bid),
                    url=entry["url"],
                    type=entry.get("type", "unknown"),
                    api_path=entry.get("api_path", "/v1"),
                    status="unknown",
                )
        except Exception as e:
            logger.warning(f"Failed to parse LLM_STATIC_BACKENDS: {e}")

    async def _discover_gateway_pods(self):
        """Discover pods created by the LLM Gateway pod manager.

        Scans all backend namespaces for Running pods with the gateway-managed
        label. Registers Ollama pods with the Ollama client for per-node routing.
        """
        from app.services.llm_ollama_client import ollama_client
        from app.services.llm_pod_manager import GATEWAY_LABEL, GATEWAY_LABEL_VALUE, BACKEND_NAMESPACES

        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            discovered = set()

            for backend_type, default_ns in BACKEND_NAMESPACES.items():
                namespace = os.getenv(
                    {
                        "ollama": "LLM_OLLAMA_NAMESPACE",
                        "vllm": "LLM_VLLM_NAMESPACE",
                        "tensorrt-llm": "LLM_TENSORRT_NAMESPACE",
                    }.get(backend_type, ""),
                    default_ns,
                )

                try:
                    pods = v1.list_namespaced_pod(
                        namespace,
                        label_selector=f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}",
                    )
                except Exception:
                    continue

                port = self._get_backend_port(backend_type)

                for pod in pods.items:
                    if pod.status.phase != "Running" or not pod.status.pod_ip:
                        continue
                    node_name = pod.spec.node_name
                    if not node_name:
                        continue

                    pod_ip = pod.status.pod_ip
                    pod_name = pod.metadata.name
                    backend_id = f"{backend_type}-{node_name}"
                    discovered.add(backend_id)

                    api_path = "/v1"
                    if backend_type == "ollama":
                        url = f"http://{pod_ip}:11434"
                        display = f"Ollama ({node_name})"
                        ollama_client.register_node(node_name, pod_ip, pod_name)
                    elif backend_type == "vllm":
                        url = f"http://{pod_ip}:{port}"
                        display = f"vLLM ({node_name})"
                    else:
                        url = f"http://{pod_ip}:{port}"
                        display = f"TRT-LLM ({node_name})"

                    self._backends[backend_id] = BackendEntry(
                        id=backend_id,
                        name=display,
                        url=url,
                        type=backend_type,
                        api_path=api_path,
                        status="unknown",
                        node=node_name,
                    )

            stale = [
                bid for bid in list(self._backends)
                if any(bid.startswith(f"{bt}-") for bt in BACKEND_NAMESPACES)
                and bid not in discovered
                and self._backends[bid].node
            ]
            for bid in stale:
                backend = self._backends[bid]
                if backend.type == "ollama" and backend.node:
                    ollama_client.unregister_node(backend.node)
                del self._backends[bid]

        except Exception as e:
            logger.warning(f"Gateway pod discovery failed: {e}")

    def _get_backend_port(self, backend_type: str) -> int:
        ports = {"ollama": 11434, "vllm": 7860, "tensorrt-llm": 7860}
        return ports.get(backend_type, 8080)

    async def _discover_from_configmaps(self):
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            cms = v1.list_config_map_for_all_namespaces(
                label_selector="thinkube.io/managed=true"
            )

            for cm in cms.items:
                if cm.metadata.name != "thinkube-service-config":
                    continue
                if not cm.data or "service.yaml" not in cm.data:
                    continue

                svc_data = yaml.safe_load(cm.data["service.yaml"])
                namespace = cm.metadata.namespace

                endpoints = svc_data.get("endpoints", {})
                for ep_name, ep_data in endpoints.items():
                    url = ep_data.get("internal_url") or ep_data.get("url", "")
                    if not url:
                        continue

                    server_type = self._detect_server_type(svc_data, namespace)
                    if server_type:
                        bid = f"{namespace}-{ep_name}"
                        if bid not in self._backends:
                            self._backends[bid] = BackendEntry(
                                id=bid,
                                name=f"{namespace}/{ep_name}",
                                url=url,
                                type=server_type,
                                api_path="/v1",
                                status="unknown",
                            )
        except Exception as e:
            logger.warning(f"ConfigMap discovery failed: {e}")

    def _detect_server_type(self, svc_data: dict, namespace: str) -> Optional[str]:
        template_type = svc_data.get("template_type", "")
        ns_lower = namespace.lower()

        if "vllm" in template_type or "vllm" in ns_lower:
            return "vllm"
        if "tensorrt" in template_type or "trtllm" in ns_lower:
            return "tensorrt-llm"
        if "ollama" in template_type or "ollama" in ns_lower:
            return "ollama"
        return None

    async def _probe_all(self):
        tasks = [self._probe_backend(b) for b in list(self._backends.values())]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe_backend(self, backend: BackendEntry):
        from datetime import datetime
        try:
            if backend.type == "ollama":
                await self._probe_ollama(backend)
            else:
                await self._probe_openai_compatible(backend)
        except Exception as e:
            backend.status = "unhealthy"
            backend.models = []
            logger.debug(f"Backend {backend.id} probe failed: {e}")
        finally:
            backend.last_probe = datetime.utcnow().isoformat()

    async def _probe_ollama(self, backend: BackendEntry):
        resp = await self._client.get(f"{backend.url}/api/tags")
        if resp.status_code != 200:
            backend.status = "unhealthy"
            backend.models = []
            return

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

    async def _probe_openai_compatible(self, backend: BackendEntry):
        health_url = f"{backend.url}/health"
        try:
            resp = await self._client.get(health_url)
            if resp.status_code != 200:
                backend.status = "unhealthy"
                backend.models = []
                return
        except Exception:
            backend.status = "unhealthy"
            backend.models = []
            return

        models_url = f"{backend.url}/v1/models"
        try:
            resp = await self._client.get(models_url)
            if resp.status_code == 200:
                data = resp.json()
                model_ids = [
                    m.get("id", "") for m in data.get("data", []) if m.get("id")
                ]
                backend.models = model_ids
            else:
                backend.models = []
        except Exception:
            backend.models = []

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
