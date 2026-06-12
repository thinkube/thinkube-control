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
    "text-embeddings": "text-embeddings",
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
            "text-embeddings": "LLM_TEI_NAMESPACE",
        }
        env_key = ns_env.get(backend_type)
        if env_key:
            return os.getenv(env_key, BACKEND_NAMESPACES.get(backend_type, backend_type))
        return BACKEND_NAMESPACES.get(backend_type, backend_type)

    def _get_base_deployment_name(self, backend_type: str) -> str:
        names = {
            "ollama": "ollama",
            "vllm": "vllm-inference",
            "tensorrt-llm": "tensorrt-inference",
            "text-embeddings": "text-embeddings-inference",
        }
        return names.get(backend_type, backend_type)

    def _make_deployment_name(self, backend_type: str, node_name: str) -> str:
        base = self._get_base_deployment_name(backend_type)
        return f"{base}-{node_name}"

    def _get_container_env(self, deployment, env_name: str) -> Optional[str]:
        for container in deployment.spec.template.spec.containers:
            if container.env:
                for env_var in container.env:
                    if env_var.name == env_name:
                        return env_var.value
        return None

    def get_managed_pod(self, backend_type: str, node_name: str) -> Optional[ManagedPod]:
        key = f"{backend_type}-{node_name}"
        return self._managed.get(key)

    def list_managed_pods(self, backend_type: Optional[str] = None) -> List[ManagedPod]:
        pods = list(self._managed.values())
        if backend_type:
            pods = [p for p in pods if p.backend_type == backend_type]
        return pods

    async def ensure_pod(
        self, backend_type: str, node_name: str, gpu_count: int = 1,
        model_env: Optional[Dict[str, str]] = None,
        mem_limit_gb: Optional[float] = None,
        wait_ready: bool = True,
    ) -> Tuple[bool, Optional[ManagedPod]]:
        """Ensure a pod is running for this backend on the given node.

        Returns (success, managed_pod). If wait_ready is False, creates the
        Deployment and returns immediately without waiting for pod readiness.
        """
        key = f"{backend_type}-{node_name}"

        existing = self._managed.get(key)
        if existing and existing.ready and existing.pod_ip:
            loop = asyncio.get_event_loop()
            pod_info = await loop.run_in_executor(
                _k8s_executor,
                self._find_pod_for_deployment,
                existing.namespace,
                self._get_base_deployment_name(backend_type),
                node_name,
            )
            if pod_info and pod_info[2]:
                restarted = await loop.run_in_executor(
                    _k8s_executor,
                    self._check_and_sync_image,
                    backend_type,
                    node_name,
                )
                if restarted:
                    logger.info(f"Image updated for {key}, waiting for new pod")
                    self._managed.pop(key, None)
                    if not wait_ready:
                        return True, None
                    pod = await self._wait_for_pod_ready(backend_type, node_name)
                    if pod:
                        self._managed[key] = pod
                        return True, pod
                    return False, None
                existing.pod_ip = pod_info[0]
                existing.pod_name = pod_info[1]
                return True, existing
            logger.info(f"Cached pod for {key} no longer exists, recreating")
            self._managed.pop(key, None)

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
                model_env,
                mem_limit_gb,
            )
            if not success:
                return False, None

            if not wait_ready:
                logger.info(f"Deployment created for {key}, not waiting for readiness")
                return True, None

            pod = await self._wait_for_pod_ready(backend_type, node_name)
            if pod:
                self._managed[key] = pod
                return True, pod
            logger.warning(f"Pod for {key} never became ready, deleting zombie deployment")
            await asyncio.get_event_loop().run_in_executor(
                _k8s_executor,
                self._delete_deployment,
                self._get_namespace(backend_type),
                self._make_deployment_name(backend_type, node_name),
            )
            return False, None
        finally:
            event.set()
            self._creating.pop(key, None)

    def _create_node_deployment(
        self, backend_type: str, node_name: str, gpu_count: int,
        model_env: Optional[Dict[str, str]] = None,
        mem_limit_gb: Optional[float] = None,
    ) -> bool:
        """Create a single-replica Deployment targeting a specific node.

        Copies the pod template from the base Deployment (replicas: 0) and adds
        nodeSelector + GPU resource requests. If model_env is provided, injects
        env vars (MODEL_ID, etc.) so the container auto-loads the model on start.
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

            base = apps_v1.read_namespaced_deployment(base_name, namespace)

            try:
                existing = apps_v1.read_namespaced_deployment(deploy_name, namespace)
                if existing:
                    needs_recreate = False
                    if model_env and model_env.get("MODEL_ID"):
                        existing_model_id = self._get_container_env(existing, "MODEL_ID")
                        if existing_model_id and existing_model_id != model_env["MODEL_ID"]:
                            needs_recreate = True

                    if not needs_recreate:
                        pod_status, _ = self.check_pod_status(backend_type, node_name)
                        if pod_status == "failed":
                            needs_recreate = True

                    if needs_recreate:
                        apps_v1.delete_namespaced_deployment(deploy_name, namespace)
                    else:
                        self._sync_deployment_image(apps_v1, existing, base, namespace)
                        if model_env:
                            self._sync_deployment_env(apps_v1, existing, namespace, model_env)
                        if (existing.spec.replicas or 0) < 1:
                            apps_v1.patch_namespaced_deployment(
                                deploy_name, namespace,
                                {"spec": {"replicas": 1}}
                            )
                            logger.info(f"Scaled {deploy_name} to 1 replica")
                        return True
            except client.rest.ApiException as e:
                if e.status != 404:
                    raise
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

            # Architecture-aware memory sizing: on UMA the model's GPU memory is
            # host RAM charged to this cgroup, so the limit must cover it; on
            # discrete GPUs a modest host-overhead limit is enough.
            if mem_limit_gb:
                mem = f"{int(round(mem_limit_gb))}Gi"
                req_mem = f"{max(int(round(mem_limit_gb / 2)), 1)}Gi"
                for container in pod_template.spec.containers:
                    requests = container.resources.requests or {}
                    limits = container.resources.limits or {}
                    limits["memory"] = mem
                    requests["memory"] = req_mem
                    container.resources.requests = requests
                    container.resources.limits = limits

            if model_env:
                for container in pod_template.spec.containers:
                    existing_env = list(container.env or [])
                    for env_name, env_value in model_env.items():
                        existing_env.append(
                            client.V1EnvVar(name=env_name, value=str(env_value))
                        )
                    container.env = existing_env

            selector_labels = {
                GATEWAY_LABEL: GATEWAY_LABEL_VALUE,
                BACKEND_TYPE_LABEL: backend_type,
                "thinkube.io/target-node": node_name,
            }

            labels = pod_template.metadata.labels or {}
            labels.update(selector_labels)
            pod_template.metadata.labels = labels

            deployment = client.V1Deployment(
                api_version="apps/v1",
                kind="Deployment",
                metadata=client.V1ObjectMeta(
                    name=deploy_name,
                    namespace=namespace,
                    labels=dict(selector_labels, **{
                        "app.kubernetes.io/name": base_name,
                    }),
                    annotations={
                        "argocd.argoproj.io/compare-options": "IgnoreExtraneous",
                    },
                ),
                spec=client.V1DeploymentSpec(
                    replicas=1,
                    selector=client.V1LabelSelector(
                        match_labels=selector_labels,
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

    def _check_and_sync_image(self, backend_type: str, node_name: str) -> bool:
        """Check if the node deployment image matches the base and patch if not.

        Returns True if the image was updated (pod will restart).
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

            base = apps_v1.read_namespaced_deployment(base_name, namespace)
            node_deploy = apps_v1.read_namespaced_deployment(deploy_name, namespace)

            base_images = {c.name: c.image for c in base.spec.template.spec.containers}
            patches = []
            for container in node_deploy.spec.template.spec.containers:
                base_image = base_images.get(container.name)
                if base_image and container.image != base_image:
                    patches.append({"name": container.name, "image": base_image})
                    logger.info(
                        f"Image drift: {deploy_name}/{container.name} "
                        f"{container.image} → {base_image}"
                    )
            if patches:
                apps_v1.patch_namespaced_deployment(
                    deploy_name,
                    namespace,
                    {"spec": {"template": {"spec": {"containers": patches}}}},
                )
                logger.info(f"Synced images for {deploy_name}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Image sync check failed for {backend_type}/{node_name}: {e}")
            return False

    def _sync_deployment_image(self, apps_v1, node_deploy, base_deploy, namespace: str):
        """Patch node deployment if its container images don't match the base."""
        base_images = {c.name: c.image for c in base_deploy.spec.template.spec.containers}
        patches = []
        for container in node_deploy.spec.template.spec.containers:
            base_image = base_images.get(container.name)
            if base_image and container.image != base_image:
                patches.append({"name": container.name, "image": base_image})
                logger.info(
                    f"Image drift: {node_deploy.metadata.name}/{container.name} "
                    f"{container.image} → {base_image}"
                )
        if patches:
            apps_v1.patch_namespaced_deployment(
                node_deploy.metadata.name,
                namespace,
                {"spec": {"template": {"spec": {"containers": patches}}}},
            )
            logger.info(f"Synced images for {node_deploy.metadata.name}")

    def _sync_deployment_env(self, apps_v1, node_deploy, namespace: str, model_env: Dict[str, str]):
        """Update env vars on an existing deployment to match the requested model_env."""
        container = node_deploy.spec.template.spec.containers[0]
        existing_env = {e.name: e.value for e in (container.env or []) if e.value is not None}
        patch_vars = []
        for env_name, env_value in model_env.items():
            if existing_env.get(env_name) != str(env_value):
                patch_vars.append({"name": env_name, "value": str(env_value)})
        if patch_vars:
            apps_v1.patch_namespaced_deployment(
                node_deploy.metadata.name,
                namespace,
                {"spec": {"template": {"spec": {"containers": [
                    {"name": container.name, "env": patch_vars}
                ]}}}},
            )
            logger.info(f"Synced env vars for {node_deploy.metadata.name}: {[v['name'] for v in patch_vars]}")

    async def _wait_for_pod_ready(
        self, backend_type: str, node_name: str, timeout: int = 600
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
            selector = f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE},thinkube.io/target-node={node_name}"
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

    def check_pod_status(self, backend_type: str, node_name: str) -> tuple[str, str]:
        """Check the actual K8s pod status for a backend/node.

        Returns (status, detail) where status is one of:
        "ready", "progressing", "failed", "absent".
        """
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = client.AppsV1Api()
            namespace = self._get_namespace(backend_type)
            deploy_name = self._make_deployment_name(backend_type, node_name)

            try:
                apps_v1.read_namespaced_deployment(deploy_name, namespace)
            except client.rest.ApiException as e:
                if e.status == 404:
                    return "absent", ""
                raise

            v1 = client.CoreV1Api()
            selector = f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE},thinkube.io/target-node={node_name}"
            pods = v1.list_namespaced_pod(namespace, label_selector=selector)

            if not pods.items:
                return "progressing", ""

            for pod in pods.items:
                statuses = pod.status.container_statuses or []
                for cs in statuses:
                    if cs.state and cs.state.waiting:
                        reason = cs.state.waiting.reason or ""
                        message = cs.state.waiting.message or ""
                        if reason in ("CrashLoopBackOff", "ErrImagePull", "ImagePullBackOff", "CreateContainerError"):
                            detail = reason
                            if message:
                                detail = f"{reason}: {message}"
                            elif cs.last_state and cs.last_state.terminated:
                                t = cs.last_state.terminated
                                detail = f"{reason} (exit {t.exit_code})"
                                if t.message:
                                    detail = f"{reason}: {t.message}"
                            return "failed", detail

                if pod.status.phase == "Running":
                    conditions = pod.status.conditions or []
                    ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
                    if ready:
                        return "ready", ""
                elif pod.status.phase == "Failed":
                    return "failed", pod.status.reason or ""

            return "progressing", ""
        except Exception as e:
            logger.debug(f"Pod status check failed for {backend_type}/{node_name}: {e}")
            return "progressing", ""

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
        """Discover existing gateway-managed Deployments and sync images on startup."""
        try:
            loop = asyncio.get_event_loop()
            pods = await loop.run_in_executor(_k8s_executor, self._reconcile_sync)
            for pod in pods:
                key = f"{pod.backend_type}-{pod.node_name}"
                self._managed[key] = pod
            if pods:
                logger.info(f"Reconciled {len(pods)} gateway-managed pods")
        except Exception as e:
            logger.error(f"Pod reconciliation failed: {e}")

    def _reconcile_sync(self) -> List[ManagedPod]:
        """List gateway deployments and sync their images with base deployments."""
        results = []
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = client.AppsV1Api()

            for backend_type in BACKEND_NAMESPACES:
                ns = self._get_namespace(backend_type)
                base_name = self._get_base_deployment_name(backend_type)

                try:
                    base = apps_v1.read_namespaced_deployment(base_name, ns)
                except Exception:
                    continue

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

                    self._sync_deployment_image(apps_v1, deploy, base, ns)

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
