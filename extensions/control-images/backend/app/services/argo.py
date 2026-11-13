# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Mirrors and builds run as Argo Workflows in the namespace argo, as the
account image-builder and with its registry login (the secret docker-config):
the image builder every app build of the platform uses.

A workflow outlives this backend. The backend records the workflow's name with
the job or the build, and follows it again after a restart.
"""

from dataclasses import dataclass
from typing import Any, Dict

from kubernetes import client, config
from kubernetes.client.rest import ApiException

NAMESPACE = "argo"
SERVICE_ACCOUNT = "image-builder"
REGISTRY_LOGIN_SECRET = "docker-config"
# The platform's image with Buildah, in Harbor's library project
BUILDAH_IMAGE = "library/buildah:v1.43.4"
FINAL_PHASES = ("Succeeded", "Failed", "Error")
GROUP, VERSION, PLURAL = "argoproj.io", "v1alpha1", "workflows"
# A finished workflow is kept a day: the backend reads its log when it next follows it
KEEP_SECONDS = 24 * 3600


class WorkflowGone(RuntimeError):
    """The workflow no longer exists in the cluster."""


@dataclass
class WorkflowState:
    # Pending, Running, Succeeded, Failed or Error
    phase: str
    message: str
    # The log of the workflow's pod so far
    log: str

    @property
    def ended(self) -> bool:
        return self.phase in FINAL_PHASES


def _apis():
    config.load_incluster_config()
    return client.CustomObjectsApi(), client.CoreV1Api()


def submit(workflow: Dict[str, Any]) -> str:
    """Create the workflow; its name."""
    custom, _ = _apis()
    created = custom.create_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, workflow)
    return created["metadata"]["name"]


def state(name: str) -> WorkflowState:
    """The workflow's phase and message, and its pod's log so far. Raises WorkflowGone when it does not exist."""
    custom, core = _apis()
    try:
        workflow = custom.get_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, name)
    except ApiException as e:
        if e.status == 404:
            raise WorkflowGone(f"the workflow {NAMESPACE}/{name} no longer exists") from e
        raise
    status = workflow.get("status") or {}
    pods = core.list_namespaced_pod(NAMESPACE, label_selector=f"workflows.argoproj.io/workflow={name}").items
    log = ""
    if pods and pods[0].status.phase != "Pending":
        response = core.read_namespaced_pod_log(pods[0].metadata.name, NAMESPACE, container="main",
                                                _preload_content=False)
        log = response.data.decode("utf-8", errors="replace")
    return WorkflowState(status.get("phase") or "Pending", status.get("message") or "", log)


def delete_labelled(label: str, value: str) -> None:
    """Delete every workflow that carries the label with this value."""
    custom, _ = _apis()
    found = custom.list_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, label_selector=f"{label}={value}")
    for workflow in found["items"]:
        custom.delete_namespaced_custom_object(GROUP, VERSION, NAMESPACE, PLURAL, workflow["metadata"]["name"])


def node_architectures() -> list:
    """The architectures of the cluster's nodes, sorted."""
    _, core = _apis()
    return sorted({node.metadata.labels["kubernetes.io/arch"] for node in core.list_node().items})
