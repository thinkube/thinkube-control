# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A template deploy after its push: the build, the sync, the registration.

deploy_application.py ends once its push has started a build, and prints the
facts of that build (scripts/deploy_log.py) which the run keeps in its `build`
column: when it pushed, the build's workflow, the discovery ConfigMap it
rendered into k8s/ and the deploy date written in it. From those facts the
backend follows the run to its outcome:

1. The build workflow ends. Failed or Error is the run's failure.
2. Argo CD has applied this deploy's discovery ConfigMap: the one in the
   cluster carries this deploy's date.
3. Service discovery registers the app.

Nothing here lives only in the process that started the deploy, so a backend
that starts while a run is at any of these steps follows it from the facts.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

ARGO_NAMESPACE = "argo"
APP_LABEL = "thinkube.io/app-name"
FINISHED = ("Succeeded", "Failed", "Error")
POLL_SECONDS = 5
# The webhook starts the build about a second after the push
WORKFLOW_START_SECONDS = 120
# After the build, the Harbor webhook adapter commits the new images and has
# Argo CD sync; that takes seconds, and a sync waits for any sync in progress
CONFIGMAP_SECONDS = 600

Log = Callable[[str, str], None]


class BuildFailed(Exception):
    """The run's outcome is a failure, for the reason given."""


def _clients():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CustomObjectsApi(), client.CoreV1Api()


def build_workflow(workflows: List[Dict[str, Any]], pushed_at: str) -> Optional[str]:
    """The workflow a push started: the first of the app's created at or after it."""
    pushed = datetime.fromisoformat(pushed_at)
    started = [
        w for w in workflows
        if datetime.fromisoformat(w["metadata"]["creationTimestamp"].replace("Z", "+00:00")) >= pushed
    ]
    started.sort(key=lambda w: w["metadata"]["creationTimestamp"])
    return started[0]["metadata"]["name"] if started else None


def node_reports(workflow: Dict[str, Any], reported: set) -> List[Tuple[str, str]]:
    """The (log type, message) of each step that changed phase since the last report."""
    reports = []
    for node in (workflow.get("status") or {}).get("nodes", {}).values():
        name = node.get("displayName") or node.get("name", "unknown")
        phase = node.get("phase")
        key = f"{name}:{phase}"
        if key in reported:
            continue
        reported.add(key)
        if phase == "Running":
            reports.append(("info", f"  ⚙️  {name}: Running"))
        elif phase == "Succeeded":
            reports.append(("info", f"  ✅ {name}: Succeeded"))
        elif phase in ("Failed", "Error"):
            reports.append(("error", f"  ❌ {name}: {phase}"))
    return reports


def deployed_at(configmap_data: Optional[Dict[str, str]]) -> Optional[str]:
    """The deploy date a discovery ConfigMap carries."""
    content = (configmap_data or {}).get("service.yaml")
    if not content:
        return None
    service = (yaml.safe_load(content) or {}).get("service") or {}
    return (service.get("metadata") or {}).get("deployment_date")


async def _find_workflow(custom, app_name: str, pushed_at: str) -> str:
    deadline = asyncio.get_event_loop().time() + WORKFLOW_START_SECONDS
    while True:
        listed = await asyncio.to_thread(
            custom.list_namespaced_custom_object,
            "argoproj.io", "v1alpha1", ARGO_NAMESPACE, "workflows",
            label_selector=f"{APP_LABEL}={app_name}",
        )
        name = build_workflow(listed.get("items", []), pushed_at)
        if name:
            return name
        if asyncio.get_event_loop().time() > deadline:
            raise BuildFailed(
                f"No build workflow of {app_name} started within {WORKFLOW_START_SECONDS}s of the push at {pushed_at}"
            )
        await asyncio.sleep(POLL_SECONDS)


async def _wait_for_workflow(custom, name: str, log: Log) -> None:
    reported: set = set()
    while True:
        try:
            workflow = await asyncio.to_thread(
                custom.get_namespaced_custom_object,
                "argoproj.io", "v1alpha1", ARGO_NAMESPACE, "workflows", name,
            )
        except ApiException as e:
            raise BuildFailed(f"Cannot read build workflow {name}: {e.status} {e.reason}") from e
        for kind, message in node_reports(workflow, reported):
            log(kind, message)
        status = workflow.get("status") or {}
        phase = status.get("phase")
        if phase == "Succeeded":
            log("success", f"🎉 Build workflow {name} succeeded")
            return
        if phase in FINISHED:
            raise BuildFailed(f"Build workflow {name} {phase}: {status.get('message', 'no message')}")
        await asyncio.sleep(POLL_SECONDS)


async def _wait_for_configmap(core, where: str, deploy_date: str, log: Log) -> None:
    namespace, name = where.split("/", 1)
    log("info", f"Waiting for Argo CD to apply ConfigMap {where} of this deploy")
    deadline = asyncio.get_event_loop().time() + CONFIGMAP_SECONDS
    while True:
        try:
            configmap = await asyncio.to_thread(core.read_namespaced_config_map, name, namespace)
            found = deployed_at(configmap.data)
        except ApiException as e:
            if e.status != 404:
                raise BuildFailed(f"Cannot read ConfigMap {where}: {e.status} {e.reason}") from e
            found = None
        if found == deploy_date:
            log("success", f"Argo CD applied ConfigMap {where}")
            return
        if asyncio.get_event_loop().time() > deadline:
            raise BuildFailed(
                f"Argo CD has not applied ConfigMap {where} of this deploy (deployment_date {deploy_date}) "
                f"within {CONFIGMAP_SECONDS}s of the build's end; it holds "
                f"{'deployment_date ' + found if found else 'no deploy of its own'}. "
                f"Look at the Argo CD application's sync"
            )
        await asyncio.sleep(POLL_SECONDS)


def _register(domain: str) -> None:
    from app.db.session import SessionLocal
    from app.services.discovery import ServiceDiscovery

    db = SessionLocal()()
    try:
        ServiceDiscovery(db, domain).discover_all()
    finally:
        db.close()


async def follow(app_name: str, build: Dict[str, str], domain: str, log: Log,
                 record_workflow: Callable[[str], None]) -> None:
    """Follow a pushed deploy to its end; BuildFailed says why it failed."""
    missing = [fact for fact in ("pushed_at", "discovery", "deployed_at") if not build.get(fact)]
    if missing:
        raise BuildFailed(f"The deploy recorded no {', '.join(missing)} of its build; deploy the app again")
    custom, core = _clients()
    workflow = build.get("workflow")
    if not workflow:
        workflow = await _find_workflow(custom, app_name, build["pushed_at"])
        record_workflow(workflow)
        log("info", f"Build workflow of the push: {workflow}")
    await _wait_for_workflow(custom, workflow, log)
    await _wait_for_configmap(core, build["discovery"], build["deployed_at"], log)
    await asyncio.to_thread(_register, domain)
    log("success", f"Service discovery registered {app_name}")
