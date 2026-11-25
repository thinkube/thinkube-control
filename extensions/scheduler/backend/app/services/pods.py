# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The pods of the scheduler's jobs: a Kubernetes job in the scheduler's namespace for each start of a job, on
the job's node.

The pod asks Kubernetes for its memory and CPU, and for no GPU: the scheduler names the GPUs it is given in
NVIDIA_VISIBLE_DEVICES, by their ids, and the pod runs with the runtime class that takes ids (GPU_RUNTIME_CLASS).
A job without a GPU is given none ("void"), whatever its image says. Stopping a job deletes its Kubernetes job:
the process gets SIGTERM and the job's stop_seconds to end.
"""

import asyncio
from typing import Any, Dict, List, Optional, Set

from app.core.config import settings
from app.models.jobs import HOLD_LABEL, Job

# The label that names the scheduler's job a Kubernetes job belongs to
JOB_LABEL = "scheduler.thinkube.io/job"
# The runtime class with which NVIDIA_VISIBLE_DEVICES takes a GPU's id (seen on tkamd2 and tkspark; the classes
# nvidia and nvidia-cdi refuse an id)
GPU_RUNTIME_CLASS = "nvidia-legacy"
# A finished Kubernetes job stays a day, for its pod's log
KEEP_FINISHED_SECONDS = 24 * 3600
# The mark Thinkube Control's training mode puts on its node; a job runs there when the scheduler placed it there
TRAINING_TOLERATION = {"key": "thinkube.io/training", "operator": "Equal", "value": "true", "effect": "NoSchedule"}
# The folders a job may mount, by name: the claim of the scheduler's namespace or the node's folder, where it is
# mounted, and whether read-only. They are where a notebook server and Mill's main pod have them.
MOUNTS: Dict[str, Dict[str, Any]] = {
    "shared": {"claim": "thinkube-shared", "path": "/storage/thinkube-shared", "read_only": False},
    "models": {"claim": "models", "path": "/home/thinkube/thinkube/models", "read_only": False},
    # Written by a job that registers a model: the platform's registry stages it there
    "mlflow": {"claim": "mlflow-root", "path": "/home/thinkube/thinkube/mlflow", "read_only": False},
    # The Python environments built on each node
    "venvs": {"host_path": "/var/lib/jupyterhub-venvs", "path": "/var/lib/jupyterhub-venvs", "read_only": True},
}
# What a pod that is not yet running may say, read into the job's waiting line
PENDING_REASONS = ("Unschedulable", "ErrImagePull", "ImagePullBackOff", "CreateContainerConfigError")


class PodsError(Exception):
    """Kubernetes refused; the message says what."""


def pod_job_name(job: Job, start: int) -> str:
    return f"job-{job.id[:8]}-{start}"


def manifest(job: Job, start: int, gpu_ids: List[str]) -> Dict[str, Any]:
    """The Kubernetes job of one start of a job, on the GPUs it was given."""
    limits = {"cpu": str(job.cpu_cores), "memory": f"{job.memory_gb}Gi"}
    env = [{"name": name, "value": str(value)} for name, value in sorted(job.env.items())
           if name not in ("NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES")]
    env.append({"name": "NVIDIA_VISIBLE_DEVICES", "value": ",".join(gpu_ids) if gpu_ids else "void"})
    env.append({"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "compute,utility"})
    env.append({"name": "SCHEDULER_JOB_ID", "value": job.id})
    env.append({"name": "SCHEDULER_START", "value": str(start)})
    # Shared memory for the processes of one job, such as the processes of a training on several GPUs; it counts
    # in the pod's memory
    mounts = [{"name": "shm", "mountPath": "/dev/shm"}]
    volumes: List[Dict[str, Any]] = [{"name": "shm", "emptyDir": {"medium": "Memory"}}]
    for name in job.mounts:
        mount = MOUNTS[name]
        mounts.append({"name": name, "mountPath": mount["path"], "readOnly": mount["read_only"]})
        if "claim" in mount:
            volumes.append({"name": name, "persistentVolumeClaim": {"claimName": mount["claim"],
                                                                     "readOnly": mount["read_only"]}})
        else:
            volumes.append({"name": name, "hostPath": {"path": mount["host_path"], "type": "Directory"}})
    container: Dict[str, Any] = {
        "name": "job",
        "image": f"{settings.REGISTRY}/{job.image}",
        "command": list(job.command),
        "env": env,
        # The node keeps the job's memory, which cannot be shared, and half its CPU cores, which can: the job uses
        # all of them while the node has them free, as a notebook server does
        "resources": {"requests": {"cpu": f"{job.cpu_cores * 500}m", "memory": limits["memory"]},
                      "limits": limits},
        "volumeMounts": mounts,
        # A process that fails says why in its last lines: they are the job's error
        "terminationMessagePolicy": "FallbackToLogsOnError",
    }
    if job.working_dir:
        container["workingDir"] = job.working_dir
    spec: Dict[str, Any] = {
        "restartPolicy": "Never",
        "nodeSelector": {"kubernetes.io/hostname": job.node},
        "tolerations": [TRAINING_TOLERATION],
        "terminationGracePeriodSeconds": job.stop_seconds,
        "securityContext": {"runAsUser": settings.SCHEDULER_JOB_UID, "runAsGroup": settings.SCHEDULER_JOB_GID},
        "containers": [container],
        "volumes": volumes,
    }
    if gpu_ids:
        spec["runtimeClassName"] = GPU_RUNTIME_CLASS
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": pod_job_name(job, start), "namespace": settings.SCHEDULER_RUNS_NAMESPACE,
                     "labels": {JOB_LABEL: job.id, "app.kubernetes.io/managed-by": "scheduler"}},
        "spec": {
            # A failed job is the owner's to start again
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": KEEP_FINISHED_SECONDS,
            "template": {"metadata": {"labels": {JOB_LABEL: job.id}}, "spec": spec},
        },
    }


class Pods:
    """The Kubernetes side of the jobs, as the worker calls it."""

    def __init__(self) -> None:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self._batch = client.BatchV1Api()
        self._core = client.CoreV1Api()

    async def _call(self, what: str, fn, *args, **kwargs):
        from kubernetes.client.rest import ApiException

        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except ApiException as e:
            raise PodsError(f"Kubernetes refused to {what}: {e.status} {e.reason}: {e.body}") from e

    async def start(self, job: Job, start: int, gpu_ids: List[str]) -> str:
        made = manifest(job, start, gpu_ids)
        await self._call(f"create the job {made['metadata']['name']}", self._batch.create_namespaced_job,
                         settings.SCHEDULER_RUNS_NAMESPACE, made)
        return made["metadata"]["name"]

    async def _pod(self, name: str):
        pods = await self._call(f"list the pods of the job {name}", self._core.list_namespaced_pod,
                                settings.SCHEDULER_RUNS_NAMESPACE, label_selector=f"job-name={name}")
        return pods.items[0] if pods.items else None

    async def state(self, name: str) -> Dict[str, Any]:
        """The Kubernetes job's state: "running", "completed", "failed" or "gone", with what its pod says:
        `waiting`, why a pod that is not running yet waits, and `error`, why a failed one failed."""
        from kubernetes.client.rest import ApiException

        try:
            found = await asyncio.to_thread(self._batch.read_namespaced_job, name, settings.SCHEDULER_RUNS_NAMESPACE)
        except ApiException as e:
            if e.status == 404:
                return {"state": "gone"}
            raise PodsError(f"Kubernetes refused to read the job {name}: {e.status} {e.reason}") from e
        if found.status.succeeded:
            return {"state": "completed"}
        pod = await self._pod(name)
        if found.status.failed:
            return {"state": "failed", "error": _exit(pod)}
        return {"state": "running", "waiting": _waiting(pod)}

    async def stop(self, name: str) -> None:
        """Delete the Kubernetes job and its pod; the process gets SIGTERM and the job's stop_seconds to end."""
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        try:
            await asyncio.to_thread(self._batch.delete_namespaced_job, name, settings.SCHEDULER_RUNS_NAMESPACE,
                                    body=client.V1DeleteOptions(propagation_policy="Background"))
        except ApiException as e:
            if e.status != 404:
                raise PodsError(f"Kubernetes refused to delete the job {name}: {e.status} {e.reason}") from e

    async def pod_gone(self, name: str) -> bool:
        """Whether a stopped job's pod has ended and gone, so that its GPUs are free."""
        return await self._pod(name) is None

    async def holds_with_pod(self, namespace: str) -> Set[str]:
        """The ids of the holds that have a pod in the namespace: the pods that carry HOLD_LABEL and have not
        ended."""
        pods = await self._call(f"list the pods of the holds in {namespace}", self._core.list_namespaced_pod,
                                namespace, label_selector=HOLD_LABEL)
        return {p.metadata.labels[HOLD_LABEL] for p in pods.items if p.status.phase not in ("Succeeded", "Failed")}

    async def log(self, name: str, lines: int) -> Optional[str]:
        """The last lines the job's pod wrote; None when the pod is gone."""
        pod = await self._pod(name)
        if pod is None:
            return None
        # The raw answer, decoded here: the client gives a log back as the text of a bytes value otherwise
        answer = await self._call(f"read the log of {pod.metadata.name}", self._core.read_namespaced_pod_log,
                                  pod.metadata.name, settings.SCHEDULER_RUNS_NAMESPACE, tail_lines=lines,
                                  _preload_content=False)
        return answer.data.decode("utf-8", errors="replace")


def _exit(pod: Any) -> str:
    """Why a pod failed: its container's exit code and reason, or the pod's own reason."""
    if pod is None:
        return "the job failed and its pod is gone"
    for container in pod.status.container_statuses or []:
        ended = container.state.terminated if container.state else None
        if ended is not None:
            why = f"the job's process exited with code {ended.exit_code}"
            if ended.reason and ended.reason not in ("Error", "Completed"):
                why += f" ({ended.reason})"
            if ended.message:
                why += f": {ended.message.strip()[-500:]}"
            return why
    return f"the pod failed: {pod.status.reason or pod.status.phase}: {pod.status.message or 'no message'}"


def _waiting(pod: Any) -> Optional[str]:
    """Why a pod that is not running waits: it cannot be placed on its node, or its image cannot be pulled."""
    if pod is None or pod.status.phase != "Pending":
        return None
    for condition in pod.status.conditions or []:
        if condition.type == "PodScheduled" and condition.status == "False" and condition.reason in PENDING_REASONS:
            return f"the pod waits for room on its node: {condition.message}"
    for container in pod.status.container_statuses or []:
        waiting = container.state.waiting if container.state else None
        if waiting is not None and waiting.reason in PENDING_REASONS:
            return f"the pod cannot start: {waiting.reason}: {waiting.message or 'no message'}"
    return None
