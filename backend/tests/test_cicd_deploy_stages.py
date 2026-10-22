# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A pipeline's deploy stage ends when Kubernetes marks the new ReplicaSet
available, succeeds only when no pod of the earlier version is left, and fails
when the new pods do not start."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import app.api.cicd as cicd  # noqa: E402

SYNC_START, SYNC_END = "2026-10-22T08:25:35Z", "2026-10-22T08:25:51Z"


def at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class Custom:
    def get_namespaced_custom_object(self, **kwargs):
        return {"status": {"operationState": {"startedAt": SYNC_START, "finishedAt": SYNC_END, "phase": "Succeeded"},
                           "resources": [{"kind": "Deployment", "group": "apps", "name": "forge-backend"}]}}


def deployment(total, progressing):
    return NS(
        metadata=NS(name="forge-backend", namespace="forge", generation=2),
        spec=NS(replicas=1, selector=NS(match_labels={"app": "forge-backend"})),
        status=NS(updated_replicas=1, available_replicas=1, replicas=total, observed_generation=2,
                  conditions=[NS(type="Available", status="True", reason="MinimumReplicasAvailable",
                                 last_update_time=at("2026-10-21T01:13:05Z"), last_transition_time=at("2026-10-21T01:13:05Z")),
                              progressing]),
    )


def pod(name, waiting=None):
    state = NS(waiting=NS(reason=waiting, message="back-off restarting failed container") if waiting else None)
    return NS(metadata=NS(name=name, creation_timestamp=at("2026-10-22T08:25:52Z")),
              spec=NS(containers=[NS(name="backend", image="r/forge-backend:new")]),
              status=NS(container_statuses=[NS(name="backend", state=state, restart_count=6 if waiting else 0)]))


def stages(dep, pods):
    apps = NS(read_namespaced_deployment=lambda name, namespace: dep)
    core = NS(list_namespaced_pod=lambda namespace, label_selector: NS(items=pods))
    return cicd._get_argocd_deploy_stages(Custom(), core, apps, "forge", "forge", None)


def test_a_finished_rollout_ends_when_the_new_replicaset_became_available():
    done = NS(type="Progressing", status="True", reason="NewReplicaSetAvailable",
              last_update_time=at("2026-10-22T08:26:10Z"))
    deploy = stages(deployment(1, done), [pod("forge-backend-new")])[1]
    assert deploy["status"] == "SUCCEEDED"
    assert deploy["completedAt"] == at("2026-10-22T08:26:10Z").timestamp()
    assert deploy["duration"] == 35
    assert deploy["podName"] == "forge-backend-new"


def test_new_pods_that_crash_fail_the_stage_while_the_old_pod_still_serves():
    rolling = NS(type="Progressing", status="True", reason="ReplicaSetUpdated", last_update_time=at("2026-10-22T08:26:00Z"))
    deploy = stages(deployment(2, rolling), [pod("forge-backend-old"), pod("forge-backend-new", "CrashLoopBackOff")])[1]
    assert deploy["status"] == "FAILED"
    assert "CrashLoopBackOff" in deploy["errorMessage"]
    assert deploy["podName"] == "forge-backend-new"
    assert deploy["duration"] is None
