# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The GPUs of the apps, asked of the Scheduler.

A container of an app that declares gpu in thinkube.yaml is rendered into a Deployment that carries
GPUS_ANNOTATION and GPU_MEMORY_ANNOTATION, is pinned to a node, and reads NVIDIA_VISIBLE_DEVICES from its
ConfigMap <deployment>-gpus (scripts/gpu_placement.py). Its pod asks Kubernetes for no GPU, and cannot start while
the ConfigMap holds no GPU ids. Thinkube Control owns the hold of each such Deployment; every TICK_SECONDS it:

- asks the Scheduler for a hold for each one that has replicas and no open hold: on its node, with the GPUs and the
  GPU memory its annotations say, and the priority a person set at the Scheduler for apps (KIND);
- writes the ids of the GPUs a running hold was given into the ConfigMap, and deletes the Deployment's pods that
  are not running, so that they start at once;
- when the Scheduler asks for the GPUs back (the hold is stopping), takes the ids out of the ConfigMap and deletes
  the Deployment's pods, which then wait without starting; once none of them runs, it releases the hold, which
  waits for its turn again;
- ends a hold whose Deployment is gone, has no replicas, or no longer carries GPUS_ANNOTATION.

The hold's id is kept on the ConfigMap (HOLD_ANNOTATION). A hold the Scheduler failed (its node can never run it)
is asked again only once the Deployment changed (its generation), and the Scheduler's reason is kept on the
ConfigMap (ERROR_ANNOTATION). The replicas of a Deployment are never changed here: an Argo CD sync of the app
leaves all of this as it is.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SCHEDULER_URL = "http://scheduler-backend.thinkube-control.svc.cluster.local:8000/internal/scheduler"
OWNER = "thinkube-control"
# The kind of work an app with a GPU is, whose priority a person sets at the Scheduler
KIND = "app"
# Where the Scheduler asks Control to give an app's GPUs up (app.api.internal)
RELEASE_URL = "http://backend.thinkube-control.svc.cluster.local:8000/internal/apps/gpus/release"
GPUS_ANNOTATION = "scheduler.thinkube.io/gpus"
GPU_MEMORY_ANNOTATION = "scheduler.thinkube.io/gpu-memory-gb"
HOLD_ANNOTATION = "scheduler.thinkube.io/hold"
GENERATION_ANNOTATION = "scheduler.thinkube.io/generation"
ERROR_ANNOTATION = "scheduler.thinkube.io/error"
IDS_KEY = "NVIDIA_VISIBLE_DEVICES"
OPEN = ("waiting", "running", "stopping")
TICK_SECONDS = 10

_wake = asyncio.Event()


class SchedulerError(Exception):
    """The Scheduler refused or did not answer; the message says which."""


def configmap_name(deployment: str) -> str:
    return f"{deployment}-gpus"


def hold_name(namespace: str, deployment: str) -> str:
    return f"{namespace}/{deployment}"


class Scheduler:
    """The Scheduler's routes for the parts of the platform."""

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Dict[str, Any]:
        try:
            with httpx.Client(timeout=30.0) as http:
                answer = http.request(method, SCHEDULER_URL + path, json=body)
        except httpx.HTTPError as e:
            raise SchedulerError(f"the Scheduler did not answer ({method} {path}): {e}") from e
        if answer.status_code >= 400:
            raise SchedulerError(f"the Scheduler refused ({method} {path}): {answer.status_code} {answer.text[:300]}")
        return answer.json()

    def open_holds(self) -> List[Dict[str, Any]]:
        return self._call("GET", f"/jobs?owner={OWNER}")["jobs"]

    def get(self, hold_id: str) -> Dict[str, Any]:
        return self._call("GET", f"/jobs/{hold_id}")

    def priority(self, kind: str) -> int:
        """The priority of a kind of work, as a person set it at the Scheduler."""
        return self._call("GET", "/priorities")[kind]

    def ask(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", "/holds", request)

    def end(self, hold_id: str) -> None:
        self._call("POST", f"/jobs/{hold_id}/stop")

    def release(self, hold_id: str) -> None:
        self._call("POST", f"/jobs/{hold_id}/release")


class Cluster:
    """The Kubernetes side: the Deployments that ask for GPUs, their ConfigMaps and their pods."""

    def __init__(self) -> None:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self._apps = client.AppsV1Api()
        self._core = client.CoreV1Api()

    def deployments(self) -> List[Any]:
        return [d for d in self._apps.list_deployment_for_all_namespaces().items
                if GPUS_ANNOTATION in (d.metadata.annotations or {})]

    def configmap(self, namespace: str, name: str) -> Optional[Any]:
        from kubernetes.client.rest import ApiException

        try:
            return self._core.read_namespaced_config_map(name, namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def write_configmap(self, namespace: str, name: str, annotations: Dict[str, Optional[str]],
                        ids: Optional[str]) -> None:
        """Create or replace the ConfigMap: these annotations (None removes one), and the GPU ids, or none."""
        from kubernetes import client

        current = self.configmap(namespace, name)
        kept = dict((current.metadata.annotations or {}) if current is not None else {})
        for key, value in annotations.items():
            if value is None:
                kept.pop(key, None)
            else:
                kept[key] = value
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace, annotations=kept,
                                         labels={"app.kubernetes.io/managed-by": "thinkube-control"}),
            data={IDS_KEY: ids} if ids else {})
        if current is None:
            self._core.create_namespaced_config_map(namespace, body)
        else:
            body.metadata.resource_version = current.metadata.resource_version
            self._core.replace_namespaced_config_map(name, namespace, body)

    def pods(self, deployment: Any) -> List[Any]:
        labels = deployment.spec.selector.match_labels or {}
        selector = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return self._core.list_namespaced_pod(deployment.metadata.namespace, label_selector=selector).items

    def delete_pod(self, pod: Any) -> None:
        from kubernetes.client.rest import ApiException

        try:
            self._core.delete_namespaced_pod(pod.metadata.name, pod.metadata.namespace)
        except ApiException as e:
            if e.status != 404:
                raise


def running(pod: Any) -> bool:
    """Whether a pod's containers run, or may: a pod that waits for its GPU ids has none running."""
    if pod.status is None or pod.status.phase in ("Succeeded", "Failed"):
        return False
    return any(s.state is not None and s.state.running is not None for s in pod.status.container_statuses or [])


def memory_limit_gb(deployment: Any) -> int:
    """The memory limit of the Deployment's containers, in whole GiB: on a node whose GPU takes its memory from
    the system's, the hold counts it beside the GPU memory."""
    total = 0.0
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
    for container in deployment.spec.template.spec.containers:
        limit = str(((container.resources.limits or {}) if container.resources else {}).get("memory", "0"))
        factor = next((f for unit, f in units.items() if limit.endswith(unit)), 1)
        number = limit[:-2] if limit[-2:] in units else limit[:-1] if limit[-1:] in units else limit
        total += float(number) * factor
    return int(-(-total // 2**30))


class AppGpus:
    def __init__(self, cluster: Cluster, scheduler: Scheduler) -> None:
        self.cluster, self.scheduler = cluster, scheduler

    def tick(self) -> None:
        holds = {h["id"]: h for h in self.scheduler.open_holds()}
        wanted = set()
        for deployment in self.cluster.deployments():
            try:
                hold_id = self._follow(deployment, holds)
            except Exception as e:  # one app's fault does not stop the others'; it is logged with the app's name
                logger.exception("the GPUs of %s/%s: %s", deployment.metadata.namespace, deployment.metadata.name, e)
                hold_id = (self._annotations(deployment) or {}).get(HOLD_ANNOTATION)
            if hold_id:
                wanted.add(hold_id)
        for hold in holds.values():
            if hold["id"] not in wanted:
                logger.info("ending hold %s (%s): its Deployment no longer asks for GPUs", hold["id"], hold["name"])
                self.scheduler.end(hold["id"])

    def _annotations(self, deployment: Any) -> Dict[str, str]:
        found = self.cluster.configmap(deployment.metadata.namespace, configmap_name(deployment.metadata.name))
        return dict((found.metadata.annotations or {}) if found is not None else {})

    def _follow(self, deployment: Any, holds: Dict[str, Dict[str, Any]]) -> Optional[str]:
        """Bring one Deployment's hold and ConfigMap along; the id of its hold while it should hold GPUs."""
        namespace, name = deployment.metadata.namespace, deployment.metadata.name
        cm_name = configmap_name(name)
        found = self.cluster.configmap(namespace, cm_name)
        annotations = dict((found.metadata.annotations or {}) if found is not None else {})
        ids = ((found.data or {}) if found is not None else {}).get(IDS_KEY)
        hold_id = annotations.get(HOLD_ANNOTATION)
        hold = holds.get(hold_id) if hold_id else None
        if hold_id and hold is None:
            hold = self.scheduler.get(hold_id)
        node = (deployment.spec.template.spec.node_selector or {}).get("kubernetes.io/hostname")
        generation = str(deployment.metadata.generation)

        if not deployment.spec.replicas or node is None:
            # Nothing to run, or nowhere: an open hold ends, and the GPU ids go
            if hold is not None and hold["status"] in OPEN:
                self.scheduler.end(hold["id"])
                # Ended here, it is not ended again with the holds no Deployment wants
                holds.pop(hold["id"], None)
            if found is not None and (ids or hold_id):
                error = None if node else ("the Deployment names no node; deploy the app again so that its "
                                           "manifests name one")
                self.cluster.write_configmap(namespace, cm_name, {HOLD_ANNOTATION: None, ERROR_ANNOTATION: error},
                                             None)
            return None

        if hold is None or hold["status"] not in OPEN:
            if hold is not None and hold["status"] == "failed" and annotations.get(GENERATION_ANNOTATION) == generation:
                return None
            gpu_memory = (deployment.metadata.annotations or {}).get(GPU_MEMORY_ANNOTATION)
            made = self.scheduler.ask({
                "name": hold_name(namespace, name), "owner": OWNER, "node": node,
                "gpus": int(deployment.metadata.annotations[GPUS_ANNOTATION]),
                "gpu_memory_gb": float(gpu_memory) if gpu_memory else None,
                "memory_gb": memory_limit_gb(deployment), "priority": self.scheduler.priority(KIND), "release_url": RELEASE_URL})
            logger.info("asked hold %s for %s/%s on %s", made["id"], namespace, name, node)
            self.cluster.write_configmap(namespace, cm_name, {HOLD_ANNOTATION: made["id"],
                                                              GENERATION_ANNOTATION: generation,
                                                              ERROR_ANNOTATION: None}, None)
            return made["id"]

        if hold["status"] == "running":
            given = ",".join(hold["gpu_ids"])
            if ids != given:
                self.cluster.write_configmap(namespace, cm_name, {ERROR_ANNOTATION: None}, given)
                logger.info("hold %s of %s/%s gave the GPUs %s", hold["id"], namespace, name, given)
            for pod in self.cluster.pods(deployment):
                if not running(pod) and pod.metadata.deletion_timestamp is None:
                    # It waited for its GPU ids; started again, it reads them
                    self.cluster.delete_pod(pod)
        elif hold["status"] == "stopping":
            # The Scheduler asked for the GPUs: the pods stop, and wait without starting
            if ids:
                self.cluster.write_configmap(namespace, cm_name, {}, None)
            pods = self.cluster.pods(deployment)
            for pod in pods:
                if running(pod) and pod.metadata.deletion_timestamp is None:
                    self.cluster.delete_pod(pod)
            if not any(running(pod) for pod in pods):
                self.scheduler.release(hold["id"])
                logger.info("released hold %s of %s/%s for %s", hold["id"], namespace, name, hold.get("preempted_by"))
        elif ids:
            # Waiting for its turn: the pods have no GPU ids to start with
            self.cluster.write_configmap(namespace, cm_name, {}, None)
        return hold["id"]


def wake() -> None:
    """Look at the apps' GPUs now: the Scheduler asked for some."""
    _wake.set()


async def run_forever() -> None:
    worker: Optional[AppGpus] = None
    while True:
        try:
            if worker is None:
                worker = AppGpus(Cluster(), Scheduler())
            await asyncio.to_thread(worker.tick)
        except Exception as e:  # the loop goes on; what failed is logged
            logger.warning("the apps' GPUs could not be brought along: %s", e)
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
