# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""
LLM Pod Manager

Creates and deletes per-node Deployments for gateway-managed inference backends.
Each backend type (llm_backend_types) has a base Deployment at replicas: 0. When
a user loads a model, this manager creates a single-replica Deployment targeting
the chosen node and GPU. Unloading the last model on a pod deletes that Deployment.
"""

import asyncio
import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from app.services import gpu_holds
from app.services.llm_backend_types import llm_backend_types

logger = logging.getLogger(__name__)

_k8s_executor = ThreadPoolExecutor(max_workers=4)

GATEWAY_LABEL = "thinkube.io/managed-by"
GATEWAY_LABEL_VALUE = "llm-gateway"
BACKEND_TYPE_LABEL = "thinkube.io/backend-type"
TARGET_NODE_LABEL = "thinkube.io/target-node"
MODEL_LABEL = "thinkube.io/model"

# A shared backend (Ollama) hosts many models in one pod per node. Every other
# backend runs one model per pod, so it gets one Deployment per model on a
# node, and the model is part of that Deployment's name, labels and selector.

_SLUG_MAX = 40

# The memory-backed /dev/shm of a pod on several GPUs.
SHM_VOLUME = "dshm"
SHM_SIZE = "2Gi"


def model_slug(model_id: str) -> str:
    """A model id as a Kubernetes name part: lowercase letters, digits and hyphens.

    Long ids are cut and given a short hash of the full id, so two models
    never share a slug.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")
    if len(slug) > _SLUG_MAX:
        digest = hashlib.sha1(model_id.encode()).hexdigest()[:6]
        slug = f"{slug[: _SLUG_MAX - 7].rstrip('-')}-{digest}"
    return slug


def deployment_gpu_request(deployment) -> int:
    """The nvidia.com/gpu limit of a Deployment's pods, summed over its containers."""
    total = 0
    for container in deployment.spec.template.spec.containers:
        limits = (container.resources.limits if container.resources else None) or {}
        total += int(limits.get("nvidia.com/gpu", 0))
    return total




def untolerated_taint(tolerations: Optional[list], taints: Optional[list]) -> Optional[str]:
    """The first of the node's taints that keeps a pod with these tolerations off it, or None.

    A NoSchedule or NoExecute taint keeps a pod off unless one of its tolerations
    matches it: the same key, or no key with operator Exists; operator Exists, or
    Equal (the default) with the same value; and no effect, or the taint's.
    PreferNoSchedule only steers the scheduler.
    """
    for taint in taints or []:
        if taint.get("effect") not in ("NoSchedule", "NoExecute"):
            continue
        tolerated = False
        for t in tolerations or []:
            operator = t.get("operator") or "Equal"
            key_matches = t.get("key") == taint.get("key") or (not t.get("key") and operator == "Exists")
            value_matches = operator == "Exists" or (t.get("value") or "") == (taint.get("value") or "")
            effect_matches = not t.get("effect") or t.get("effect") == taint.get("effect")
            if key_matches and value_matches and effect_matches:
                tolerated = True
                break
        if not tolerated:
            value = f"={taint['value']}" if taint.get("value") else ""
            return f"{taint.get('key')}{value}:{taint.get('effect')}"
    return None


def unmet_required_affinity(affinity: Optional[dict], node_labels: Dict[str, str]) -> Optional[str]:
    """Why a node cannot take a pod whose template has this affinity, or None when it can.

    Evaluates the required node affinity the scheduler enforces
    (requiredDuringSchedulingIgnoredDuringExecution): the node must match one of the
    nodeSelectorTerms, and a term matches when all its matchExpressions do. The affinity
    is the camelCase form of the Kubernetes API. A term with matchFields is not decided
    here and counts as matching.
    """
    required = ((affinity or {}).get("nodeAffinity") or {}).get("requiredDuringSchedulingIgnoredDuringExecution")
    terms = (required or {}).get("nodeSelectorTerms") or []
    if not terms:
        return None

    def unmet(expr: dict) -> Optional[str]:
        key, op, values = expr["key"], expr["operator"], list(expr.get("values") or [])
        has = key in node_labels
        value = node_labels.get(key)
        if op == "In" and value in values or op == "NotIn" and value not in values:
            return None
        if op == "Exists" and has or op == "DoesNotExist" and not has:
            return None
        if op in ("Gt", "Lt") and has:
            try:
                n, bound = int(value), int(values[0])
            except (ValueError, IndexError):
                return f"{key} {op} {values}: the node's {key} is '{value}'"
            if (n > bound) if op == "Gt" else (n < bound):
                return None
        shown = f"{key} {op} {values}" if values else f"{key} {op}"
        return f"{shown}, and the node's {key} is {repr(value) if has else 'not set'}"

    reasons = []
    for term in terms:
        if term.get("matchFields"):
            return None
        failed = [r for r in (unmet(e) for e in term.get("matchExpressions") or []) if r]
        if not failed:
            return None
        reasons.append("; ".join(failed))
    return " or ".join(reasons)


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
        model_slug: Optional[str] = None,
    ):
        self.deployment_name = deployment_name
        self.model_slug = model_slug
        self.namespace = namespace
        self.backend_type = backend_type
        self.node_name = node_name
        self.pod_ip = pod_ip
        self.pod_name = pod_name
        self.ready = ready


class LLMPodManager:
    def __init__(self):
        self._managed: Dict[str, ManagedPod] = {}
        self._creating: Dict[str, asyncio.Event] = {}

    def _get_namespace(self, backend_type: str) -> str:
        return llm_backend_types.require(backend_type).namespace

    def _is_disabled(self, backend_type: str) -> bool:
        """Whether the operator has turned this backend off in Control's services view.

        Placement has to honour that: without the check, disabling a backend
        removes its pods and the next model load puts them straight back.
        Raises ControlUnavailableError when Control does not say: the load
        fails with that message.
        """
        from app.services.control_services import service_enabled

        return not service_enabled(self._get_namespace(backend_type))

    def _get_base_deployment_name(self, backend_type: str) -> str:
        return llm_backend_types.require(backend_type).deployment

    def _slug_for(self, backend_type: str, model_id: Optional[str]) -> Optional[str]:
        """The model part of a backend's identity; none for a shared backend."""
        if llm_backend_types.is_shared(backend_type):
            return None
        if not model_id:
            raise ValueError(f"A {backend_type} pod serves one model; the model is required")
        return model_slug(model_id)

    def gpu_slots_of(self, backend_type: str, node_name: str, model_id: str) -> int:
        """The time-slice slots a one-model pod holds: its Deployment's nvidia.com/gpu request."""
        from kubernetes import client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        deployment = client.AppsV1Api().read_namespaced_deployment(
            self._make_deployment_name(backend_type, node_name, model_id),
            self._get_namespace(backend_type),
        )
        return deployment_gpu_request(deployment)

    def node_refusal(self, backend_type: str, node_name: str) -> Optional[str]:
        """Why the backend's pods cannot be scheduled on the node, or None when they can.

        The per-model Deployment copies the base Deployment's pod template, affinity
        and tolerations included, so a node its required node affinity excludes (an
        architecture the backend's image is not built for), or a node tainted with a
        taint it does not tolerate, would leave the pod Pending.
        """
        from kubernetes import client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        base = client.AppsV1Api().read_namespaced_deployment(
            self._get_base_deployment_name(backend_type), self._get_namespace(backend_type)
        )
        api = client.ApiClient()
        affinity = api.sanitize_for_serialization(base.spec.template.spec.affinity)
        tolerations = api.sanitize_for_serialization(base.spec.template.spec.tolerations)
        node = client.CoreV1Api().read_node(node_name)
        name = llm_backend_types.require(backend_type).display_name
        unmet = unmet_required_affinity(affinity, node.metadata.labels or {})
        if unmet is not None:
            return f"{name} pods cannot run on {node_name}: its Deployment requires {unmet}"
        taint = untolerated_taint(tolerations, api.sanitize_for_serialization(node.spec.taints))
        if taint is None:
            return None
        return f"{name} pods cannot run on {node_name}: {node_name} has the taint {taint}, which they do not tolerate"

    def _make_deployment_name(self, backend_type: str, node_name: str, model_id: Optional[str] = None) -> str:
        base = self._get_base_deployment_name(backend_type)
        slug = self._slug_for(backend_type, model_id)
        return f"{base}-{node_name}-{slug}" if slug else f"{base}-{node_name}"

    def _key(self, backend_type: str, node_name: str, model_id: Optional[str] = None) -> str:
        slug = self._slug_for(backend_type, model_id)
        return f"{backend_type}-{node_name}/{slug}" if slug else f"{backend_type}-{node_name}"

    def _pod_selector(self, backend_type: str, node_name: str, model_id: Optional[str] = None) -> str:
        selector = f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE},{TARGET_NODE_LABEL}={node_name}"
        slug = self._slug_for(backend_type, model_id)
        return f"{selector},{MODEL_LABEL}={slug}" if slug else selector

    def _get_container_env(self, deployment, env_name: str) -> Optional[str]:
        for container in deployment.spec.template.spec.containers:
            if container.env:
                for env_var in container.env:
                    if env_var.name == env_name:
                        return env_var.value
        return None

    def get_managed_pod(
        self, backend_type: str, node_name: str, model_id: Optional[str] = None
    ) -> Optional[ManagedPod]:
        return self._managed.get(self._key(backend_type, node_name, model_id))

    def list_managed_pods(self, backend_type: Optional[str] = None) -> List[ManagedPod]:
        pods = list(self._managed.values())
        if backend_type:
            pods = [p for p in pods if p.backend_type == backend_type]
        return pods

    async def ensure_pod(
        self, backend_type: str, node_name: str, gpu_count: int = 1,
        model_env: Optional[Dict[str, str]] = None,
        model_id: Optional[str] = None,
        mem_limit_gb: Optional[float] = None,
        wait_ready: bool = True,
        gpu_ids: Optional[List[str]] = None,
        hold_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[ManagedPod]]:
        """Ensure a pod is running for this backend on the given node.

        ``model_id`` names the model of a one-model backend; its pod is that
        model's own Deployment.

        ``gpu_ids`` and ``hold_id``: the GPUs a hold of the Scheduler gave
        (app.services.gpu_holds). The pod is started on them by their ids and
        asks Kubernetes for no GPU; its Deployment keeps the hold's id, and
        the hold ends when the Deployment is deleted. Without them a pod with
        GPUs is not made: only a Deployment that exists already is found, as
        a further load on a pod that serves several models finds its pod.

        Returns (success, managed_pod). If wait_ready is False, creates the
        Deployment and returns immediately without waiting for pod readiness.
        """
        if self._is_disabled(backend_type):
            logger.info(
                f"Refusing to place {backend_type} on {node_name}: "
                f"the service is disabled"
            )
            return False, None

        key = self._key(backend_type, node_name, model_id)

        existing = self._managed.get(key)
        if existing and existing.ready and existing.pod_ip:
            loop = asyncio.get_event_loop()
            pod_info = await loop.run_in_executor(
                _k8s_executor,
                self._find_pod_for_deployment,
                existing.namespace,
                self._pod_selector(backend_type, node_name, model_id),
            )
            if pod_info and pod_info[2]:
                # A shared pod (Ollama, the trained heads) that serves other
                # models must not restart for a newer image on a load of one
                # more: it takes the image when it is next created. One that
                # serves none takes it now.
                restarted = False
                if not llm_backend_types.is_shared(backend_type) or not self._serves_models(backend_type, node_name):
                    restarted = await loop.run_in_executor(
                        _k8s_executor,
                        self._check_and_sync_image,
                        backend_type,
                        node_name,
                        model_id,
                    )
                if restarted:
                    logger.info(f"Image updated for {key}, waiting for new pod")
                    self._managed.pop(key, None)
                    if not wait_ready:
                        return True, None
                    pod = await self._wait_for_pod_ready(backend_type, node_name, model_id)
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
                model_id,
                gpu_ids,
                hold_id,
            )
            if not success:
                return False, None

            if not wait_ready:
                logger.info(f"Deployment created for {key}, not waiting for readiness")
                return True, None

            pod = await self._wait_for_pod_ready(backend_type, node_name, model_id)
            if pod:
                self._managed[key] = pod
                return True, pod
            logger.warning(f"Pod for {key} never became ready, deleting zombie deployment")
            await asyncio.get_event_loop().run_in_executor(
                _k8s_executor,
                self._delete_deployment,
                self._get_namespace(backend_type),
                self._make_deployment_name(backend_type, node_name, model_id),
            )
            return False, None
        finally:
            event.set()
            self._creating.pop(key, None)

    def _create_node_deployment(
        self, backend_type: str, node_name: str, gpu_count: int,
        model_env: Optional[Dict[str, str]] = None,
        mem_limit_gb: Optional[float] = None,
        model_id: Optional[str] = None,
        gpu_ids: Optional[List[str]] = None,
        hold_id: Optional[str] = None,
    ) -> bool:
        """Create a single-replica Deployment targeting a specific node.

        Copies the pod template from the base Deployment (replicas: 0) and adds
        nodeSelector and the GPUs of its hold, by their ids. If model_env is provided, injects
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
            deploy_name = self._make_deployment_name(backend_type, node_name, model_id)
            slug = self._slug_for(backend_type, model_id)

            base = apps_v1.read_namespaced_deployment(base_name, namespace)

            # A pod's GPUs are the Scheduler's to give: without a hold's, a pod with GPUs is only found, not made
            unheld = f"{deploy_name} needs GPUs, and has no hold of the Scheduler to give them; load its model again"

            try:
                existing = apps_v1.read_namespaced_deployment(deploy_name, namespace)
                if existing:
                    # A new hold gives GPUs of its own: the Deployment is made again on them
                    needs_recreate = gpu_ids is not None
                    # A Deployment on a hold asks Kubernetes for no GPU, and is not made again for that: a pod
                    # that serves several models is started once and found here at each further load
                    on_hold = bool((existing.metadata.annotations or {}).get(gpu_holds.HOLD_ANNOTATION))
                    if on_hold and gpu_ids is None and (existing.spec.replicas or 0) < 1:
                        raise ValueError(f"{deploy_name} was stopped for work of a higher priority, and starts "
                                         "again when the Scheduler gives its hold GPUs")
                    if model_env and model_env.get("MODEL_ID"):
                        existing_model_id = self._get_container_env(existing, "MODEL_ID")
                        if existing_model_id and existing_model_id != model_env["MODEL_ID"]:
                            needs_recreate = True

                    if gpu_count > 1 and not any(
                        v.name == SHM_VOLUME for v in (existing.spec.template.spec.volumes or [])
                    ):
                        needs_recreate = True

                    if not needs_recreate:
                        pod_status, _ = self.check_pod_status(backend_type, node_name, model_id)
                        if pod_status == "failed":
                            needs_recreate = True

                    if needs_recreate and gpu_count > 0 and gpu_ids is None:
                        raise ValueError(unheld)
                    if needs_recreate:
                        apps_v1.delete_namespaced_deployment(deploy_name, namespace)
                        self._end_hold_of(existing, but=hold_id)
                    else:
                        if (existing.spec.strategy and existing.spec.strategy.type) != "Recreate":
                            apps_v1.patch_namespaced_deployment(
                                deploy_name, namespace,
                                {"spec": {"strategy": {"type": "Recreate", "rollingUpdate": None}}},
                            )
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
            if gpu_count > 0 and gpu_ids is None:
                raise ValueError(unheld)
            pod_template = base.spec.template

            pod_template.spec.node_selector = {"kubernetes.io/hostname": node_name}

            # Tolerate the dedicated-ai taint so AI pods can land on a node
            # reserved for AI workloads (harmless on an untainted all-in-one node).
            tols = list(pod_template.spec.tolerations or [])
            if not any(
                getattr(t, "key", None) == "thinkube.io/workload" for t in tols
            ):
                tols.append(
                    client.V1Toleration(
                        key="thinkube.io/workload",
                        operator="Equal",
                        value="ai",
                        effect="NoSchedule",
                    )
                )
            pod_template.spec.tolerations = tols

            if gpu_ids is not None:
                # The GPUs the Scheduler gave, named to the pod by their ids; Kubernetes's own count of GPUs,
                # which the base Deployment may ask for, is not used
                pod_template.spec.runtime_class_name = gpu_holds.GPU_RUNTIME_CLASS
                for container in pod_template.spec.containers:
                    for part in (container.resources.requests, container.resources.limits):
                        if part:
                            part.pop("nvidia.com/gpu", None)
                    kept = [e for e in (container.env or []) if e.name != "NVIDIA_VISIBLE_DEVICES"]
                    kept.append(client.V1EnvVar(name="NVIDIA_VISIBLE_DEVICES", value=",".join(gpu_ids)))
                    container.env = kept

            # Tensor-parallel ranks exchange their inputs through /dev/shm,
            # which a pod gets at 64 MiB; vLLM needs more for two ranks and
            # refuses to start. The memory counts against the pod's limit.
            if gpu_count > 1:
                volumes = list(pod_template.spec.volumes or [])
                volumes.append(client.V1Volume(
                    name=SHM_VOLUME,
                    empty_dir=client.V1EmptyDirVolumeSource(medium="Memory", size_limit=SHM_SIZE),
                ))
                pod_template.spec.volumes = volumes
                for container in pod_template.spec.containers:
                    mounts = list(container.volume_mounts or [])
                    mounts.append(client.V1VolumeMount(name=SHM_VOLUME, mount_path="/dev/shm"))
                    container.volume_mounts = mounts

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
                TARGET_NODE_LABEL: node_name,
            }
            if slug:
                selector_labels[MODEL_LABEL] = slug

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
                        **({gpu_holds.HOLD_ANNOTATION: hold_id} if hold_id else {}),
                    },
                ),
                spec=client.V1DeploymentSpec(
                    replicas=1,
                    # A second copy of a model never fits beside the first,
                    # so a new pod starts only after the old one has gone.
                    strategy=client.V1DeploymentStrategy(type="Recreate"),
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

    @staticmethod
    def _serves_models(backend_type: str, node_name: str) -> bool:
        """Whether the node's shared pod of the backend holds a model the gateway loaded on it."""
        from app.services.llm_gpu_tracker import llm_gpu_tracker

        backend_id = f"{backend_type}-{node_name}"
        return any(a.backend_id == backend_id for a in llm_gpu_tracker._allocations.get(node_name, []))

    def _check_and_sync_image(self, backend_type: str, node_name: str, model_id: Optional[str] = None) -> bool:
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
            deploy_name = self._make_deployment_name(backend_type, node_name, model_id)

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
        self, backend_type: str, node_name: str, model_id: Optional[str] = None, timeout: int = 600
    ) -> Optional[ManagedPod]:
        namespace = self._get_namespace(backend_type)
        deploy_name = self._make_deployment_name(backend_type, node_name, model_id)
        selector = self._pod_selector(backend_type, node_name, model_id)

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                loop = asyncio.get_event_loop()
                pod_info = await loop.run_in_executor(
                    _k8s_executor,
                    self._find_pod_for_deployment,
                    namespace,
                    selector,
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
                            model_slug=self._slug_for(backend_type, model_id),
                        )
            except Exception as e:
                logger.debug(f"Waiting for pod {deploy_name}: {e}")
            await asyncio.sleep(5)

        logger.error(f"Timeout waiting for pod {deploy_name} to become ready")
        return None

    def _find_pod_for_deployment(
        self, namespace: str, selector: str
    ) -> Optional[Tuple[str, str, bool]]:
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            v1 = client.CoreV1Api()
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

    def check_pod_status(
        self, backend_type: str, node_name: str, model_id: Optional[str] = None
    ) -> tuple[str, str]:
        """Check the actual K8s pod status for a backend/node.

        Returns (status, detail) where status is one of:
        "ready", "progressing", "failed", "absent", or "unknown" when the
        Kubernetes API could not be read.
        """
        try:
            from kubernetes import client, config as k8s_config

            k8s_config.load_incluster_config()

            apps_v1 = client.AppsV1Api()
            namespace = self._get_namespace(backend_type)
            deploy_name = self._make_deployment_name(backend_type, node_name, model_id)

            try:
                deployment = apps_v1.read_namespaced_deployment(deploy_name, namespace)
            except client.rest.ApiException as e:
                if e.status == 404:
                    return "absent", ""
                raise

            # Kubernetes refusing to create the pod (a quota, an admission
            # rule) leaves no pod to inspect; the Deployment says why.
            for condition in (deployment.status.conditions or []) if deployment.status else []:
                if condition.type == "ReplicaFailure" and condition.status == "True":
                    return "failed", f"{condition.reason}: {condition.message}"

            v1 = client.CoreV1Api()
            pods = v1.list_namespaced_pod(
                namespace, label_selector=self._pod_selector(backend_type, node_name, model_id)
            )

            if not pods.items:
                return "progressing", ""

            for pod in pods.items:
                statuses = pod.status.container_statuses or []
                for cs in statuses:
                    if cs.state and cs.state.waiting:
                        reason = cs.state.waiting.reason or ""
                        message = cs.state.waiting.message or ""
                        if reason in ("CrashLoopBackOff", "ErrImagePull", "ImagePullBackOff", "CreateContainerError"):
                            # A crashed container's own termination message says
                            # why it stopped; the back-off message only says that
                            # it is restarted.
                            t = cs.last_state.terminated if cs.last_state else None
                            if t and t.message:
                                detail = f"{reason}: {t.message.strip()}"
                            elif message:
                                detail = f"{reason}: {message}"
                            elif t:
                                detail = f"{reason} (exit {t.exit_code})"
                            else:
                                detail = reason
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
            logger.warning(f"Pod status check failed for {backend_type}/{node_name}: {e}")
            return "unknown", str(e)

    def scale_to_zero(self, backend_type: str, node_name: str, model_id: Optional[str] = None) -> bool:
        """Scale a gateway-managed node deployment to 0, freeing its slot/budget.

        Idempotent and safe to call from reconciliation (sync): a missing or
        already-zero deployment is a no-op, and any transient K8s error is
        swallowed (simply retried on the next reconciliation cycle) — it never
        leaves a half-state or kills a serving model.
        """
        try:
            from kubernetes import client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = client.AppsV1Api()
            namespace = self._get_namespace(backend_type)
            deploy_name = self._make_deployment_name(backend_type, node_name, model_id)
            try:
                dep = apps_v1.read_namespaced_deployment(deploy_name, namespace)
            except client.rest.ApiException as e:
                if e.status == 404:
                    return False
                raise
            if (dep.spec.replicas or 0) == 0:
                return False
            # Its hold of GPUs ends with it: started again, it asks for a new one
            apps_v1.patch_namespaced_deployment(
                deploy_name, namespace,
                {"spec": {"replicas": 0}, "metadata": {"annotations": {gpu_holds.HOLD_ANNOTATION: None,
                                                                      gpu_holds.RELOAD_ANNOTATION: None,
                                                                      gpu_holds.MODELS_ANNOTATION: None}}}
            )
            self._managed.pop(self._key(backend_type, node_name, model_id), None)
            self._end_hold_of(dep)
            logger.info(f"Scaled {deploy_name} to 0 (serves no model)")
            return True
        except Exception as e:
            logger.warning(f"scale_to_zero failed for {backend_type}/{node_name}: {e}")
            return False

    async def delete_pod(self, backend_type: str, node_name: str, model_id: Optional[str] = None) -> bool:
        """Delete a gateway-managed Deployment: a model's, or a shared backend's on a node."""
        key = self._key(backend_type, node_name, model_id)
        namespace = self._get_namespace(backend_type)
        deploy_name = self._make_deployment_name(backend_type, node_name, model_id)

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
            found = apps_v1.read_namespaced_deployment(deploy_name, namespace)
            apps_v1.delete_namespaced_deployment(deploy_name, namespace)
            self._end_hold_of(found)
            return True
        except client.rest.ApiException as e:
            if e.status == 404:
                return True
            logger.error(f"K8s delete failed for {namespace}/{deploy_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Delete failed for {namespace}/{deploy_name}: {e}")
            return False

    @staticmethod
    def _end_hold_of(deployment, but: Optional[str] = None) -> None:
        """End the hold of GPUs a Deployment that is gone, or stopped, was started with; not the hold `but`. A
        hold that could not be ended stays the Scheduler's, and is said in the log."""
        hold_id = (deployment.metadata.annotations or {}).get(gpu_holds.HOLD_ANNOTATION)
        if not hold_id or hold_id == but:
            return
        try:
            gpu_holds.end_sync(hold_id)
        except gpu_holds.HoldError as e:
            logger.error(f"The hold {hold_id} of {deployment.metadata.name} was not ended: {e}")

    def give_up(self, backend_type: str, node_name: str, model_id: Optional[str] = None,
                models: Optional[List[str]] = None) -> Optional[str]:
        """Stop a pod for work of a higher priority, to start it again later: its Deployment is kept at no
        replicas, with its hold's id and RELOAD_ANNOTATION. `models`: of a pod that serves several, the ids of the
        models on it, kept on the Deployment to load again. Returns the hold's id; None when the Deployment has no
        hold or is gone."""
        from kubernetes import client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        apps_v1 = client.AppsV1Api()
        namespace = self._get_namespace(backend_type)
        deploy_name = self._make_deployment_name(backend_type, node_name, model_id)
        try:
            dep = apps_v1.read_namespaced_deployment(deploy_name, namespace)
        except client.rest.ApiException as e:
            if e.status == 404:
                return None
            raise
        hold_id = (dep.metadata.annotations or {}).get(gpu_holds.HOLD_ANNOTATION)
        if not hold_id:
            return None
        marks = {gpu_holds.RELOAD_ANNOTATION: "true"}
        if models is not None and (dep.spec.replicas or 0) > 0:
            # Asked again once the pod is stopped, the models kept the first time stay
            marks[gpu_holds.MODELS_ANNOTATION] = json.dumps(models)
        apps_v1.patch_namespaced_deployment(
            deploy_name, namespace, {"spec": {"replicas": 0}, "metadata": {"annotations": marks}})
        self._managed.pop(self._key(backend_type, node_name, model_id), None)
        logger.info(f"Scaled {deploy_name} to 0 for work of a higher priority; it starts again when its hold "
                    f"{hold_id} has GPUs")
        return hold_id

    def start_again(self) -> List[Dict[str, Any]]:
        """Start the pods stopped by give_up whose holds have GPUs again, on those GPUs. Returns each one started:
        {"model"} for a pod of one model (its MODEL_ID), {"backend_type", "node", "models"} for a pod that serves
        several, with the ids of the models to load on it again. A Deployment whose hold has ended starts no
        more: its marks are removed."""
        from kubernetes import client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        apps_v1 = client.AppsV1Api()
        started: List[Dict[str, Any]] = []
        for backend in llm_backend_types.list():
            try:
                deploys = apps_v1.list_namespaced_deployment(
                    backend.namespace, label_selector=f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}")
            except client.rest.ApiException as e:
                if e.status == 404:
                    continue  # the backend is not installed
                raise
            for dep in deploys.items:
                annotations = dep.metadata.annotations or {}
                hold_id = annotations.get(gpu_holds.HOLD_ANNOTATION)
                if not hold_id or annotations.get(gpu_holds.RELOAD_ANNOTATION) != "true":
                    continue
                try:
                    hold = gpu_holds.get_sync(hold_id)
                except gpu_holds.HoldError as e:
                    logger.warning(f"{dep.metadata.name} waits to start again: {e}")
                    continue
                if hold["status"] in ("waiting", "stopping"):
                    continue
                if hold["status"] != "running":
                    apps_v1.patch_namespaced_deployment(
                        dep.metadata.name, backend.namespace,
                        {"metadata": {"annotations": {gpu_holds.HOLD_ANNOTATION: None,
                                                      gpu_holds.RELOAD_ANNOTATION: None,
                                                      gpu_holds.MODELS_ANNOTATION: None}}})
                    logger.info(f"{dep.metadata.name} starts no more: its hold {hold_id} is {hold['status']}")
                    continue
                containers = [{"name": c.name, "env": [{"name": "NVIDIA_VISIBLE_DEVICES",
                                                        "value": ",".join(hold["gpu_ids"])}]}
                              for c in dep.spec.template.spec.containers]
                apps_v1.patch_namespaced_deployment(
                    dep.metadata.name, backend.namespace,
                    {"metadata": {"annotations": {gpu_holds.RELOAD_ANNOTATION: None,
                                                  gpu_holds.MODELS_ANNOTATION: None}},
                     "spec": {"replicas": 1, "template": {"spec": {"containers": containers}}}})
                logger.info(f"Started {dep.metadata.name} again on {hold['gpu_ids']}")
                labels = dep.metadata.labels or {}
                if labels.get(MODEL_LABEL):
                    model = self._get_container_env(dep, "MODEL_ID")
                    if model:
                        started.append({"model": model})
                else:
                    started.append({"backend_type": backend.type, "node": labels.get(TARGET_NODE_LABEL, ""),
                                    "models": json.loads(annotations.get(gpu_holds.MODELS_ANNOTATION) or "[]")})
        return started

    def shared_pod(self, backend_type: str, node_name: str) -> Tuple[bool, Optional[str]]:
        """Of a node's pod that serves several models: (whether its Deployment runs, the id of its hold of GPUs).
        A Deployment that runs without a hold asked Kubernetes for its GPU."""
        from kubernetes import client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        try:
            dep = client.AppsV1Api().read_namespaced_deployment(
                self._make_deployment_name(backend_type, node_name, None), self._get_namespace(backend_type))
        except client.rest.ApiException as e:
            if e.status == 404:
                return False, None
            raise
        if (dep.spec.replicas or 0) < 1:
            return False, None
        return True, (dep.metadata.annotations or {}).get(gpu_holds.HOLD_ANNOTATION)

    def shared_holds(self) -> List[Tuple[str, str, str]]:
        """(backend type, node, hold id) of every running pod that serves several models and has a hold."""
        from kubernetes import client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        apps_v1 = client.AppsV1Api()
        found = []
        for backend in llm_backend_types.list():
            if not backend.shared:
                continue
            try:
                deploys = apps_v1.list_namespaced_deployment(
                    backend.namespace, label_selector=f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}")
            except client.rest.ApiException as e:
                if e.status == 404:
                    continue  # the backend is not installed
                raise
            for dep in deploys.items:
                hold_id = (dep.metadata.annotations or {}).get(gpu_holds.HOLD_ANNOTATION)
                node = (dep.metadata.labels or {}).get(TARGET_NODE_LABEL)
                if hold_id and node and (dep.spec.replicas or 0) > 0:
                    found.append((backend.type, node, hold_id))
        return found

    def end_orphan_holds(self) -> List[str]:
        """End the gateway's holds that none of its Deployments is on, ORPHAN_SECONDS after they were asked for.
        Returns their ids."""
        from datetime import datetime, timezone

        from kubernetes import client, config as k8s_config

        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        apps_v1 = client.AppsV1Api()
        kept = set()
        for backend in llm_backend_types.list():
            try:
                deploys = apps_v1.list_namespaced_deployment(
                    backend.namespace, label_selector=f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}")
            except client.rest.ApiException as e:
                if e.status == 404:
                    continue  # the backend is not installed
                raise
            kept.update((d.metadata.annotations or {}).get(gpu_holds.HOLD_ANNOTATION) for d in deploys.items)
        ended = []
        now = datetime.now(timezone.utc)
        for hold in gpu_holds.list_sync():
            age = (now - datetime.fromisoformat(hold["created_at"])).total_seconds()
            if hold["id"] not in kept and age > gpu_holds.ORPHAN_SECONDS:
                gpu_holds.end_sync(hold["id"])
                logger.warning(f"Ended the hold {hold['id']} of {hold['name']}: no Deployment is on it")
                ended.append(hold["id"])
        return ended

    async def reconcile(self):
        """Discover existing gateway-managed Deployments and sync images on startup."""
        try:
            loop = asyncio.get_event_loop()
            pods = await loop.run_in_executor(_k8s_executor, self._reconcile_sync)
            for pod in pods:
                key = (
                    f"{pod.backend_type}-{pod.node_name}/{pod.model_slug}"
                    if pod.model_slug
                    else f"{pod.backend_type}-{pod.node_name}"
                )
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

            # Running pods keep their image: a newer backend image reaches a
            # deployment at its next load (_create_node_deployment), so a
            # restart of Thinkube Control never restarts the loaded models.
            for backend in llm_backend_types.list():
                backend_type, ns = backend.type, backend.namespace
                try:
                    deploys = apps_v1.list_namespaced_deployment(
                        ns, label_selector=f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE}"
                    )
                except client.rest.ApiException as e:
                    if e.status == 404:
                        continue  # the backend is not installed
                    raise

                for deploy in deploys.items:
                    labels = deploy.metadata.labels or {}
                    node = labels.get(TARGET_NODE_LABEL, "")
                    if not node:
                        continue
                    slug = labels.get(MODEL_LABEL)

                    selector = f"{GATEWAY_LABEL}={GATEWAY_LABEL_VALUE},{TARGET_NODE_LABEL}={node}"
                    if slug:
                        selector = f"{selector},{MODEL_LABEL}={slug}"
                    pod_info = self._find_pod_for_deployment(ns, selector)
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
                        model_slug=slug,
                    ))
        except Exception as e:
            logger.error(f"Failed to list gateway deployments: {e}")
        return results


llm_pod_manager = LLMPodManager()
