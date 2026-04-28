"""
LLM Pod Manager

Creates and deletes per-node Deployments for gateway-managed inference backends.
Each backend (Ollama, vLLM, TRT-LLM) is deployed with replicas: 0. When a user
loads a model, this manager creates a single-replica Deployment targeting the
chosen node and GPU. Unloading the last model on a pod deletes that Deployment.
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_k8s_executor = ThreadPoolExecutor(max_workers=4)

GATEWAY_LABEL = "thinkube.io/managed-by"
GATEWAY_LABEL_VALUE = "llm-gateway"
BACKEND_TYPE_LABEL = "thinkube.io/backend-type"


class ManagedPod:
    """Tracks a gateway-created Deployment."""

    def __init__(
        self,
        deployment_name: str,
        namespace: str,
        backend_type: str,
        node_name: str,
        pod_ip: Optional[str] = None,
        pod_name: Optional[str] = None,
        ready: bool = False,
    ):
        self.deployment_name = deployment_name
        self.namespace = namespace
        self.backend_type = backend_type
        self.node_name = node_name
        self.pod_ip = pod_ip
        self.pod_name = pod_name
        self.ready = ready


# Backend type → namespace mapping
BACKEND_NAMESPACES = {
    "ollama": "ollama",
    "vllm": "vllm",
    "tensorrt-llm": "tensorrt",
}


class LLMPodManager:
    def __init__(self):
        self._managed: Dict[str, ManagedPod] = {}
        self._creating: Dict[str, asyncio.Event] = {}

    def _get_namespace(self, backend_type: str) -> str:
        ns_env = {
            "ollama": "LLM_OLLAMA_NAMESPACE",
            "vllm": "LLM_VLLM_NAMESPACE",
            "tensorrt-llm": "LLM_TENSORRT_NAMESPACE",
        }
        env_key = ns_env.get(backend_type)
        if env_key:
            return os.getenv(env_key, BACKEND_NAMESPACES.get(backend_type, backend_type))
        return BACKEND_NAMESPACES.get(backend_type, backend_type)

    def _get_base_deployment_name(self, backend_type: str) -> str:
        names = {
            "ollama": "ollama",
            "vllm": "vllm",
            "tensorrt-llm": "tensorrt",
        }
        return names.get(backend_type, backend_type)

    def _make_deployment_name(self, backend_type: str, node_name: str) -> str:
        base = self._get_base_deployment_name(backend_type)
        return f"{base}-{node_name}"

    def get_managed_pod(self, backend_type: str, node_name: str) -> Optional[ManagedPod]:
        key = f"{backend_type}-{node_name}"
        return self._managed.get(key)

    def list_managed_pods(self, backend_type: Optional[str] = None) -> List[ManagedPod]:
        pods = list(self._managed.values())
        if backend_type:
            pods = [p for p in pods if p.backend_type == backend_type]
        return pods

    async def ensure_pod(
        self, backend_type: str, node_name: str, gpu_count: int = 1
    ) -> Tuple[bool, Optional[ManagedPod]]:
        """Ensure a pod is running for this backend on the given node.

        Returns (success, managed_pod). If a pod already exists and is ready,
        returns immediately. Otherwise creates a new Deployment and waits for
        the pod to become ready.
        """
        key = f"{backend_type}-{node_name}"

        existing = self._managed.get(key)
        if existing and existing.ready and existing.pod_ip:
            return True, existing

        if key in self._creating:
            event = self._creating[key]
            await event.wait()
            pod = self._managed.get(key)
            if pod and pod.ready:
                return True, pod
            return False, pod

        event = asyncio.Event()
        self._creating[key] = event

        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                _k8s_executor,
                self._create_node_deployment,
                backend_type,
                node_name,
                gpu_count,
            )
            if not success:
                return False, None

            pod = await self._wait_for_pod_ready(backend_type, node_name)
            if pod:
                self._managed[key] = pod
                return True, pod
            return False, None
        finally:
            event.set()
            self._creating.pop(key, None)

    def _create_node_deployment(
        self, backend_type: str, node_name: str, gpu_count: int
    ) -> bool:
        """Create a single-replica Deployment targeting a specific node.

        Copies the pod template from the base Deployment (replicas: 0) and adds
        nodeSelector + GPU resource requests.
        """
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = client.AppsV1Api()
            namespace = self._get_namespace(backend_type)
            base_name = self._get_base_deployment_name(backend_type)
            deploy_name = self._make_deployment_name(backend_type, node_name)

            try:
                existing = apps_v1.read_namespaced_deployment(deploy_name, namespace)
                if existing:
                    logger.info(f"Deployment {deploy_name} already exists in {namespace}")
                    return True
            except client.rest.ApiException as e:
                if e.status != 404:
                    raise

            base = apps_v1.read_namespaced_deployment(base_name, namespace)
            pod_template = base.spec.template

            pod_template.spec.node_selector = {"kubernetes.io/hostname": node_name}

            if gpu_count > 0:
                for container in pod_template.spec.containers:
                    requests = container.resources.requests or {}
                    limits = container.resources.limits or {}
                    requests["nvidia.com/gpu"] = str(gpu_count)
                    limits["nvidia.com/gpu"] = str(gpu_count)
                    container.resources.requests = requests
                    container.resources.limits = limits

            labels = pod_template.metadata.labels or {}
            labels[GATEWAY_LABEL] = GATEWAY_LABEL_VALUE
            labels[BACKEND_TYPE_LABEL] = backend_type
            labels["thinkube.io/target-node"] = node_name
            pod_template.metadata.labels = labels

            deployment = client.V1Deployment(
                api_version="apps/v1",
                kind="Deployment",
                metadata=client.V1ObjectMeta(
                    name=deploy_name,
                    namespace=namespace,
                    labels={
                        "app": base_name,
                        "app.kubernetes.io/name": base_name,
                        GATEWAY_LABEL: GATEWAY_LABEL_VALUE,
                        BACKEND_TYPE_LABEL: backend_type,
                        "thinkube.io/target-node": node_name,
                    },
                    annotations={
                        "argocd.argoproj.io/compare-options": "IgnoreExtraneous",
                    },
                ),
                spec=client.V1DeploymentSpec(
                    replicas=1,
                    selector=client.V1LabelSelector(
                        match_labels={
                            "app": base_name,
                            "thinkube.io/target-node": node_name,
                        }
                    ),
                    template=pod_template,
                ),
            )

            apps_v1.create_namespaced_deployment(namespace, deployment)
            logger.info(
                f"Created gateway-managed Deployment {deploy_name} in {namespace} "
                f"(node={node_name}, gpu={gpu_count})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to create Deployment for {backend_type} on {node_name}: {e}")
            return False

    async def _wait_for_pod_ready(
        self, backend_type: str, node_name: str, timeout: int = 300
    ) -> Optional[ManagedPod]:
        namespace = self._get_namespace(backend_type)
        deploy_name = self._make_deployment_name(backend_type, node_name)
        base_name = self._get_base_deployment_name(backend_type)

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                loop = asyncio.get_event_loop()
                pod_info = await loop.run_in_executor(
                    _k8s_executor,
                    self._find_pod_for_deployment,
                    namespace,
                    base_name,
                    node_name,
                )
                if pod_info:
                    pod_ip, pod_name, ready = pod_info
                    if ready and pod_ip:
                        return ManagedPod(
                            deployment_name=deploy_name,
                            namespace=namespace,
                            backend_type=backend_type,
                            node_name=node_name,
                            pod_ip=pod_ip,
                            pod_name=pod_name,
                            ready=True,
                        )
            except Exception as e:
                logger.debug(f"Waiting for pod {deploy_name}: {e}")
            await asyncio.sleep(5)

        logger.error(f"Timeout waiting for pod {deploy_name} to become ready")
        return None

    def _find_pod_for_deployment(
        self, namespace: str, base_name: str, node_name: str
    ) -> Optional[Tuple[str, str, bool]]:
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
            selector = f"app={base_name},thinkube.io/target-node={node_name}"
            pods = v1.list_namespaced_pod(namespace, label_selector=selector)

            for pod in pods.items:
                if pod.status.phase == "Running" and pod.status.pod_ip:
                    conditions = pod.status.conditions or []
                    ready = any(
                        c.type == "Ready" and c.status == "True"
                        for c in conditions
                    )
                    return pod.status.pod_ip, pod.metadata.name, ready
            return None
        except Exception as e:
            logger.debug(f"Pod lookup failed: {e}")
            return None

    async def delete_pod(self, backend_type: str, node_name: str) -> bool:
        """Delete a gateway-managed Deployment for a backend on a node."""
        key = f"{backend_type}-{node_name}"
        namespace = self._get_namespace(backend_type)
        deploy_name = self._make_deployment_name(backend_type, node_name)

        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                _k8s_executor,
                self._delete_deployment,
                namespace,
                deploy_name,
            )
            if success:
                self._managed.pop(key, None)
                logger.info(f"Deleted gateway-managed Deployment {deploy_name} from {namespace}")
            return success
        except Exception as e:
            logger.error(f"Failed to delete Deployment {deploy_name}: {e}")
            return False

    def _delete_deployment(self, namespace: str, deploy_name: str) -> bool:
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = client.AppsV1Api()
            apps_v1.delete_namespaced_deployment(deploy_name, namespace)
            return True
        except client.rest.ApiException as e:
            if e.status == 404:
                return True
            logger.error(f"K8s delete failed for {namespace}/{deploy_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Delete failed for {namespace}/{deploy_name}: {e}")
            return False

    async def reconcile(self):
        """Discover existing gateway-managed Deployments on startup."""
        try:
            loop = asyncio.get_event_loop()
            pods = await loop.run_in_executor(_k8s_executor, self._list_gateway_deployments)
            for pod in pods:
                key = f"{pod.backend_type}-{pod.node_name}"
                self._managed[key] = pod
            if pods:
                logger.info(f"Reconciled {len(pods)} gateway-managed pods")
        except Exception as e:
            logger.error(f"Pod reconciliation failed: {e}")

    def _list_gateway_deployments(self) -> List[ManagedPod]:
        results = []
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = client.AppsV1Api()
            v1 = client.CoreV1Api()

            for backend_type, namespace in BACKEND_NAMESPACES.items():
                ns = self._get_namespace(backend_type)
                try:
                    deploys = apps_v1.list_namespaced_deployment(
                        ns, label_selector=f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}"
                    )
                except Exception:
                    continue

                for deploy in deploys.items:
                    node = (deploy.metadata.labels or {}).get("thinkube.io/target-node", "")
                    if not node:
                        continue

                    base_name = self._get_base_deployment_name(backend_type)
                    pod_info = self._find_pod_for_deployment(ns, base_name, node)
                    pod_ip = pod_info[0] if pod_info else None
                    pod_name = pod_info[1] if pod_info else None
                    ready = pod_info[2] if pod_info else False

                    results.append(ManagedPod(
                        deployment_name=deploy.metadata.name,
                        namespace=ns,
                        backend_type=backend_type,
                        node_name=node,
                        pod_ip=pod_ip,
                        pod_name=pod_name,
                        ready=ready,
                    ))
        except Exception as e:
            logger.error(f"Failed to list gateway deployments: {e}")
        return results


llm_pod_manager = LLMPodManager()
