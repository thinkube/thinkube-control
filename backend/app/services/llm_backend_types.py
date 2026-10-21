# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
LLM Backend Types

The backend types the LLM gateway serves models on. Each installed backend
declares itself in its service ConfigMap (service.llm_backend, written from
spec.llm_backend of its thinkube.yaml, or by its playbook): the base
Deployment the gateway copies per model, the port it probes, its driver and
its tier. Thinkube Control names no backend type itself.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

SERVICE_CONFIGMAP = "thinkube-service-config"
MANAGED_SELECTOR = "thinkube.io/managed=true"

# One pod serves one model, which it reads from MODEL_ID and the other
# environment variables the gateway sets on its Deployment.
DRIVER_PER_MODEL_POD = "per-model-pod"
# One pod per node serves many models, loaded through the Ollama API.
DRIVER_OLLAMA = "ollama"

TIER_PERFORMANCE = "performance"

_FIELDS = ("type", "display_name", "driver", "tier", "deployment", "port", "api_path", "health_path")


@dataclass(frozen=True)
class BackendType:
    type: str
    display_name: str
    namespace: str
    deployment: str
    port: int
    api_path: str
    health_path: str
    driver: str
    tier: str
    # CUDA compute capabilities ("12.1") the backend runs on; empty is any GPU.
    gpu_compute_capabilities: Tuple[str, ...] = ()

    def runs_on(self, compute_capability: Optional[str]) -> bool:
        return not self.gpu_compute_capabilities or compute_capability in self.gpu_compute_capabilities

    @property
    def shared(self) -> bool:
        """One pod hosts many models, so a model is not part of the pod's identity."""
        return self.driver == DRIVER_OLLAMA

    @property
    def performance(self) -> bool:
        return self.tier == TIER_PERFORMANCE


def parse_descriptor(namespace: str, descriptor: dict) -> BackendType:
    """A service ConfigMap's llm_backend block as a backend type.

    Raises ValueError naming the missing fields.
    """
    missing = [f for f in _FIELDS if not descriptor.get(f)]
    if missing:
        raise ValueError(f"llm_backend in {namespace} lacks {', '.join(missing)}")
    if descriptor["driver"] not in (DRIVER_PER_MODEL_POD, DRIVER_OLLAMA):
        raise ValueError(f"llm_backend in {namespace} has unknown driver '{descriptor['driver']}'")
    return BackendType(
        namespace=namespace,
        port=int(descriptor["port"]),
        gpu_compute_capabilities=tuple(str(c) for c in descriptor.get("gpu_compute_capabilities") or ()),
        **{f: str(descriptor[f]) for f in _FIELDS if f != "port"},
    )


class LLMBackendTypes:
    def __init__(self):
        self._types: Dict[str, BackendType] = {}

    def list(self) -> List[BackendType]:
        return list(self._types.values())

    def names(self) -> List[str]:
        return list(self._types)

    def get(self, backend_type: str) -> Optional[BackendType]:
        return self._types.get(backend_type)

    def require(self, backend_type: str) -> BackendType:
        found = self._types.get(backend_type)
        if found is None:
            raise KeyError(
                f"backend type '{backend_type}' is not installed "
                f"(installed: {', '.join(self._types) or 'none'})"
            )
        return found

    def is_shared(self, backend_type: str) -> bool:
        return self.require(backend_type).shared

    def refresh(self) -> int:
        """Read the backend types from the service ConfigMaps of every namespace.

        A failed read of the Kubernetes API keeps the types already known and
        is logged; the next refresh reads them again. A malformed descriptor
        leaves that backend out and is logged with what is wrong.
        """
        from kubernetes import client, config as k8s_config

        try:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()
            cms = client.CoreV1Api().list_config_map_for_all_namespaces(
                label_selector=MANAGED_SELECTOR
            )
        except Exception as e:
            logger.error(f"Could not read the LLM backend types from the service ConfigMaps: {e}")
            return len(self._types)

        found: Dict[str, BackendType] = {}
        for cm in cms.items:
            if cm.metadata.name != SERVICE_CONFIGMAP or "service.yaml" not in (cm.data or {}):
                continue
            namespace = cm.metadata.namespace
            try:
                service = (yaml.safe_load(cm.data["service.yaml"]) or {}).get("service") or {}
            except yaml.YAMLError as e:
                logger.warning(f"Service ConfigMap in {namespace} is not valid YAML: {e}")
                continue
            descriptor = service.get("llm_backend")
            if not descriptor:
                continue
            try:
                backend = parse_descriptor(namespace, descriptor)
            except ValueError as e:
                logger.warning(str(e))
                continue
            if backend.type in found:
                logger.warning(
                    f"Backend type '{backend.type}' is declared in {found[backend.type].namespace} "
                    f"and {namespace}; {namespace} is ignored"
                )
                continue
            found[backend.type] = backend

        if set(found) != set(self._types):
            logger.info(f"LLM backend types: {', '.join(sorted(found)) or 'none'}")
        self._types = found
        return len(found)


llm_backend_types = LLMBackendTypes()
