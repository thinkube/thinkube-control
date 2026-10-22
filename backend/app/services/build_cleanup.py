# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Removing old build workflows of apps and components.

Argo Workflows keeps every workflow and its pods until they are deleted. A
build workflow (one with the label thinkube.io/app-name) is deleted with its
pods when all of these hold:

- it is not among the KEEP_NEWEST newest builds of its app;
- it was created more than KEEP_DAYS days ago;
- it has finished;
- it did not build an image the app runs now: get_commit_rollout and the
  webhook adapter's order check find a running image's build by its tag,
  which is the workflow's UID.

The images an app runs are those Argo CD applied, `status.summary.images` of
its Application: that keeps the tag also for a Knative app, whose Deployments
carry digests. An app whose Application cannot be read keeps all its builds.
Other workflows (model mirrors, image builds, triggers) are not touched.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

ARGO_NAMESPACE = "argo"
ARGOCD_NAMESPACE = "argocd"
APP_LABEL = "thinkube.io/app-name"
PHASE_LABEL = "workflows.argoproj.io/phase"
FINISHED_PHASES = ("Succeeded", "Failed", "Error")

KEEP_NEWEST = 10
KEEP_DAYS = 7
INTERVAL_SECONDS = 3600


def deletable(builds: List[dict], running: Dict[str, Set[str]], now: datetime) -> List[dict]:
    """The builds to delete. Each build is {name, uid, app, created, phase};
    `running` maps an app to the image tags it runs, or has no entry for an
    app whose running images are not known."""
    by_app: Dict[str, List[dict]] = defaultdict(list)
    for build in builds:
        by_app[build["app"]].append(build)
    chosen = []
    for app, items in by_app.items():
        if app not in running:
            continue
        items.sort(key=lambda b: b["created"], reverse=True)
        for build in items[KEEP_NEWEST:]:
            if now - build["created"] < timedelta(days=KEEP_DAYS):
                continue
            if build["phase"] not in FINISHED_PHASES:
                continue
            if build["uid"] in running[app]:
                continue
            chosen.append(build)
    return chosen


def _clients():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CustomObjectsApi()


def _builds(custom_api) -> List[dict]:
    """The build workflows, from their metadata only: the full list carries
    every workflow's status and is megabytes."""
    response = custom_api.api_client.call_api(
        f"/apis/argoproj.io/v1alpha1/namespaces/{ARGO_NAMESPACE}/workflows",
        "GET",
        query_params=[("labelSelector", APP_LABEL)],
        header_params={"Accept": "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1"},
        auth_settings=["BearerToken"],
        _return_http_data_only=True,
        _preload_content=False,
        _request_timeout=30,
    )
    builds = []
    for item in json.loads(response.data).get("items", []):
        meta = item["metadata"]
        labels = meta.get("labels", {})
        builds.append({
            "name": meta["name"],
            "uid": meta["uid"],
            "app": labels[APP_LABEL],
            "created": datetime.fromisoformat(meta["creationTimestamp"].replace("Z", "+00:00")),
            "phase": labels.get(PHASE_LABEL, ""),
        })
    return builds


def _running_tags(custom_api, apps: Set[str]) -> Dict[str, Set[str]]:
    """The image tags each app runs, as Argo CD applied them. An app without an
    Application runs none; an app whose Application cannot be read is left out."""
    running: Dict[str, Set[str]] = {}
    for app in apps:
        try:
            application = custom_api.get_namespaced_custom_object(
                group="argoproj.io", version="v1alpha1", namespace=ARGOCD_NAMESPACE,
                plural="applications", name=app,
            )
        except ApiException as e:
            if e.status == 404:
                running[app] = set()
                continue
            logger.error(f"build clean-up: Argo CD application {app} could not be read ({e.status}); its builds are kept")
            continue
        images = application.get("status", {}).get("summary", {}).get("images", [])
        running[app] = {image.rsplit(":", 1)[-1] for image in images if f"/thinkube/{app}-" in image}
    return running


def clean_once(now: datetime = None) -> List[str]:
    """Delete the builds `deletable` chooses; returns their names."""
    now = now or datetime.now(timezone.utc)
    custom_api = _clients()
    builds = _builds(custom_api)
    running = _running_tags(custom_api, {b["app"] for b in builds})
    deleted = []
    for build in deletable(builds, running, now):
        try:
            custom_api.delete_namespaced_custom_object(
                group="argoproj.io", version="v1alpha1", namespace=ARGO_NAMESPACE,
                plural="workflows", name=build["name"],
                body=client.V1DeleteOptions(propagation_policy="Background"),
            )
        except ApiException as e:
            if e.status != 404:
                raise
        deleted.append(build["name"])
    if deleted:
        logger.info(f"build clean-up: deleted {len(deleted)} build workflows and their pods")
    return deleted
