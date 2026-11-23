# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The pods of Mill's task runs: a Kubernetes job in the namespace mill-runs
for each start of a task run, on the run's node.

The pod mounts the shared file system where Mill's main pod has it, so a
path of the tasks repository means the same in both, and runs `mill run`
with the run's task, inputs, outputs and settings. The run reports its
progress, samples and log to Mill through NATS; Forge reads only the job's
state, and a pod's exit when the job fails. Stopping a run deletes its job:
the task gets SIGTERM, ends as stopped in Mill, and its pod goes. Every
start of the run has the same state folder (state_folder), where a task
keeps what it needs to go on.
"""

import asyncio
import json
import shlex
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.forge import Run

# The label that names the run a job belongs to
RUN_LABEL = "forge.thinkube.io/run"
# Seconds a task has to end after it is asked to stop
STOP_GRACE_SECONDS = 60
# A finished job stays a day, for its pod's log
KEEP_FINISHED_SECONDS = 24 * 3600
# The mark Thinkube Control's training mode puts on its node, as a toleration
TRAINING_TOLERATION = {"key": "thinkube.io/training", "operator": "Equal", "value": "true", "effect": "NoSchedule"}
# What a pod that is not yet running may say, read into the run's waiting line
PENDING_REASONS = ("Unschedulable", "ErrImagePull", "ImagePullBackOff", "CreateContainerConfigError")


class TaskPodError(Exception):
    """Kubernetes refused or failed an operation; the message says which and why."""


def job_name(run: Run, attempt: int) -> str:
    return f"forge-task-{run.id[:8]}-{attempt}"


def mill_run_id(run: Run, attempt: int) -> str:
    """The id of the run in Mill: one for each start of the Forge run."""
    return f"forge-{run.id[:8]}-{attempt}"


def command(run: Run, run_id: str) -> List[str]:
    """`mill run` of the run's task, as the pod runs it."""
    task = run.task
    target = f"{task['file']}:{task['task']}" if task.get("task") else task["file"]
    args = ["python3", "-m", "thinkube_mill", "run", target]
    for name, path in task.get("inputs", {}).items():
        args += ["--in", f"{name}={path}"]
    for name, path in task.get("outputs", {}).items():
        args += ["--out", f"{name}={path}"]
    for name, value in task.get("settings", {}).items():
        args += ["--set", f"{name}={value if isinstance(value, str) else json.dumps(value)}"]
    if task.get("limit") is not None:
        args += ["--limit", str(task["limit"])]
    args += ["--run-id", run_id]
    return args


def state_folder(run: Run) -> str:
    """The run's state folder, on the shared file system: the same for every start of the run."""
    return f"{settings.MILL_SHARED_MOUNT}/{settings.MILL_STATE_FOLDER}/{run.id}/state"


def model_paths(run: Run) -> Dict[str, str]:
    """Each model the task names, by its id: the folder of its files as the pod mounts them."""
    return {model: f"{settings.MILL_MODELS_MOUNT}/{folder}" for model, folder in (run.task.get("models") or {}).items()}


def script(run: Run, run_id: str) -> str:
    """What the pod's shell runs: thinkube-mill installed, then the task.

    The shell stays up while the task runs, to remove the state folder when
    the task ends well: a run that completed has no more use for it. A run
    stopped or failed keeps it for its next start. The shell passes SIGTERM
    on to the task, so a task that is stopped can save its state first.

    Without a venv, Mill is installed for the image's Python, as before. With
    one, the venv of the node's architecture runs the task; Mill is installed
    without its dependencies into a folder of its own, put first on the path,
    so that it cannot replace a package of the venv (its one dependency,
    nats-py, is the venv's to have; a venv without it fails at the import,
    saying so).
    """
    venv = run.task.get("venv")
    lines = ["set -e", 'mkdir -p "$FORGE_STATE_DIR"']
    if venv:
        python = f"{settings.MILL_VENVS_MOUNT}/custom/{venv}/$ARCH/bin/python"
        lines += [
            'case "$(uname -m)" in aarch64) ARCH=arm64 ;; *) ARCH=amd64 ;; esac',
            f'PYTHON="{python}"',
            f'[ -x "$PYTHON" ] || {{ echo "the venv {venv} is not on $NODE_NAME for $ARCH ($PYTHON): '
            f'build it in Thinkube Control for this architecture" >&2; exit 1; }}',
            '"$PYTHON" -m pip install -q --no-deps --target /tmp/thinkube-mill thinkube-mill',
            "export PYTHONPATH=/tmp/thinkube-mill",
        ]
        task_command = ['"$PYTHON"'] + [shlex.quote(a) for a in command(run, run_id)[1:]]
    else:
        lines.append("pip install -q --user --break-system-packages --no-warn-script-location thinkube-mill")
        task_command = [shlex.quote(a) for a in command(run, run_id)]
    lines += [
        "set +e",
        f"{' '.join(task_command)} &",
        "child=$!",
        "trap 'kill -TERM \"$child\" 2>/dev/null' TERM",
        'wait "$child"',
        "status=$?",
        # A wait broken by SIGTERM returns early; the task is waited for until it ends
        'while kill -0 "$child" 2>/dev/null; do wait "$child"; status=$?; done',
        'if [ "$status" -eq 0 ]; then rm -rf "$FORGE_STATE_DIR"; fi',
        'exit "$status"',
    ]
    return "\n".join(lines) + "\n"


def job_manifest(run: Run, attempt: int, token: Optional[str] = None) -> Dict[str, Any]:
    """The Kubernetes job of one start of a task run.

    The task is given the LLM Gateway's address and, as THINKUBE_API_TOKEN,
    the token Forge's queue works with, so a task that calls a model can;
    FORGE_STATE_DIR, its state folder, the same for every start of the run;
    FORGE_ATTEMPT, which start this is (1 the first); and with models,
    FORGE_MODELS, a JSON object of each model's folder by its id, on MLflow's
    file system mounted read-only.
    """
    task = run.task
    platform = [{"name": "LLM_GATEWAY_URL", "value": settings.MILL_LLM_GATEWAY_URL}]
    if token:
        platform.append({"name": "THINKUBE_API_TOKEN", "value": token})
    platform += [{"name": "FORGE_RUN_ID", "value": run.id},
                 {"name": "FORGE_STATE_DIR", "value": state_folder(run)},
                 {"name": "FORGE_ATTEMPT", "value": str(attempt)}]
    models = model_paths(run)
    if models:
        platform.append({"name": "FORGE_MODELS", "value": json.dumps(models)})
    run_id = mill_run_id(run, attempt)
    limits: Dict[str, str] = {"cpu": str(run.cpu_cores), "memory": f"{run.memory_gb}Gi"}
    if task.get("gpus"):
        limits["nvidia.com/gpu"] = str(task["gpus"])
    mounts = [{"name": "shared", "mountPath": settings.MILL_SHARED_MOUNT}]
    volumes: List[Dict[str, Any]] = [{"name": "shared",
                                      "persistentVolumeClaim": {"claimName": settings.MILL_SHARED_CLAIM}}]
    if task.get("venv"):
        mounts.append({"name": "venvs", "mountPath": settings.MILL_VENVS_MOUNT, "readOnly": True})
        volumes.append({"name": "venvs", "hostPath": {"path": settings.MILL_VENVS_HOST_PATH, "type": "Directory"}})
    if models:
        mounts.append({"name": "models", "mountPath": settings.MILL_MODELS_MOUNT, "readOnly": True})
        volumes.append({"name": "models", "persistentVolumeClaim": {"claimName": settings.MILL_MODELS_CLAIM,
                                                                     "readOnly": True}})
    image = settings.MILL_VENV_IMAGE if task.get("venv") else settings.MILL_TASK_IMAGE
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name(run, attempt),
            "namespace": settings.MILL_RUNS_NAMESPACE,
            "labels": {RUN_LABEL: run.id, "app.kubernetes.io/managed-by": "forge"},
        },
        "spec": {
            # A failed task is the run's failure; Forge's queue decides whether it runs again
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": KEEP_FINISHED_SECONDS,
            "template": {
                "metadata": {"labels": {RUN_LABEL: run.id}},
                "spec": {
                    "restartPolicy": "Never",
                    "nodeSelector": {"kubernetes.io/hostname": run.node},
                    # Training mode keeps new pods off its node; Forge starts a task there only when the mode is
                    # its own (worker._place), so the task is the node's training work and tolerates the mark
                    "tolerations": [TRAINING_TOLERATION],
                    "terminationGracePeriodSeconds": STOP_GRACE_SECONDS,
                    "securityContext": {"runAsUser": settings.MILL_TASK_UID, "runAsGroup": settings.MILL_TASK_GID},
                    "containers": [{
                        "name": "task",
                        "image": f"{settings.REGISTRY}/{image}",
                        "workingDir": settings.MILL_REPOSITORY,
                        "command": ["sh", "-c", script(run, run_id)],
                        "env": [
                            {"name": "HOME", "value": "/tmp/home"},
                            {"name": "PIP_INDEX_URL", "value": settings.PACKAGE_INDEX},
                            {"name": "PYTHONUNBUFFERED", "value": "1"},
                            {"name": "MILL_RUNNER", "value": "forge"},
                            {"name": "MILL_NATS_URL", "value": settings.MILL_NATS_URL},
                            {"name": "MILL_SUBJECTS", "value": settings.MILL_SUBJECTS},
                            {"name": "NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}},
                            *platform,
                        ],
                        # Requests equal limits: the node keeps what the run was given
                        "resources": {"requests": dict(limits), "limits": limits},
                        "volumeMounts": mounts,
                    }],
                    "volumes": volumes,
                },
            },
        },
    }


class TaskPods:
    """The Kubernetes side of task runs, as the worker calls it."""

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
            raise TaskPodError(f"Kubernetes refused to {what}: {e.status} {e.reason}: {e.body}") from e

    async def start(self, run: Run, attempt: int, token: Optional[str] = None) -> str:
        manifest = job_manifest(run, attempt, token)
        await self._call(f"create the job {manifest['metadata']['name']}", self._batch.create_namespaced_job,
                         settings.MILL_RUNS_NAMESPACE, manifest)
        return manifest["metadata"]["name"]

    async def state(self, name: str) -> Dict[str, Any]:
        """The job's state: "running", "completed", "failed" or "gone", with what its pod says.

        `waiting` says why a pod that is not running yet waits, and `error`
        why a failed job failed, in the pod's own words.
        """
        from kubernetes.client.rest import ApiException

        try:
            job = await asyncio.to_thread(self._batch.read_namespaced_job, name, settings.MILL_RUNS_NAMESPACE)
        except ApiException as e:
            if e.status == 404:
                return {"state": "gone"}
            raise TaskPodError(f"Kubernetes refused to read the job {name}: {e.status} {e.reason}") from e
        status = job.status
        pods = await self._call(f"list the pods of the job {name}", self._core.list_namespaced_pod,
                                settings.MILL_RUNS_NAMESPACE, label_selector=f"job-name={name}")
        pod = pods.items[0] if pods.items else None
        if status.succeeded:
            return {"state": "completed"}
        if status.failed:
            return {"state": "failed", "error": _exit(pod)}
        return {"state": "running", "waiting": _waiting(pod)}

    async def stop(self, name: str) -> None:
        """Delete the job and its pod; the task gets SIGTERM and STOP_GRACE_SECONDS to end."""
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        try:
            await asyncio.to_thread(
                self._batch.delete_namespaced_job, name, settings.MILL_RUNS_NAMESPACE,
                body=client.V1DeleteOptions(propagation_policy="Background"),
            )
        except ApiException as e:
            if e.status != 404:
                raise TaskPodError(f"Kubernetes refused to delete the job {name}: {e.status} {e.reason}") from e


def _exit(pod: Any) -> str:
    """Why a pod failed: its container's exit code and reason, or the pod's own reason."""
    if pod is None:
        return "the job failed and its pod is gone"
    for container in pod.status.container_statuses or []:
        ended = container.state.terminated if container.state else None
        if ended is not None:
            why = f"the task's process exited with code {ended.exit_code}"
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
