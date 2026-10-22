#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A template deploy after its push is followed from facts, not from a process.

The deploy script ends once its push started a build. The backend follows the
build workflow, waits for Argo CD to apply this deploy's discovery ConfigMap,
then registers the app; any backend process can do it from the run's facts.
"""

import asyncio
from types import SimpleNamespace

import pytest
import yaml
from kubernetes.client.rest import ApiException

from app.services import build_follow as bf

PUSHED_AT = "2026-10-22T11:00:00+00:00"
DEPLOYED_AT = "2026-10-22T10:59:30+00:00"
BUILD = {
    "pushed_at": PUSHED_AT,
    "discovery": "llama-cpp/thinkube-service-config",
    "deployed_at": DEPLOYED_AT,
}


def workflow(name, created, phase="Running", nodes=None, message=None):
    status = {"phase": phase, "nodes": nodes or {}}
    if message:
        status["message"] = message
    return {"metadata": {"name": name, "creationTimestamp": created}, "status": status}


def configmap(deployed_at):
    service = {"service": {"metadata": {"deployment_date": deployed_at}}}
    return SimpleNamespace(data={"service.yaml": yaml.dump(service)})


class Custom:
    def __init__(self, listed, phases):
        self.listed = listed
        self.phases = list(phases)

    def list_namespaced_custom_object(self, *args, label_selector):
        assert label_selector == "thinkube.io/app-name=llama-cpp"
        return {"items": self.listed}

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        phase = self.phases.pop(0) if len(self.phases) > 1 else self.phases[0]
        return workflow(name, "2026-10-22T11:00:01Z", phase, message="step build failed")


class Core:
    def __init__(self, answers):
        self.answers = list(answers)

    def read_namespaced_config_map(self, name, namespace):
        assert (namespace, name) == ("llama-cpp", "thinkube-service-config")
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return configmap(answer)


@pytest.fixture
def follow(monkeypatch):
    registered = []
    monkeypatch.setattr(bf, "POLL_SECONDS", 0)
    monkeypatch.setattr(bf, "_register", lambda domain: registered.append(domain))

    def run(custom, core, build=BUILD):
        monkeypatch.setattr(bf, "_clients", lambda: (custom, core))
        logs, recorded = [], []
        asyncio.run(bf.follow("llama-cpp", build, "example.com", lambda kind, m: logs.append((kind, m)), recorded.append))
        return logs, recorded, registered

    return run


def test_the_build_is_the_first_workflow_created_after_the_push():
    workflows = [
        workflow("llama-cpp-build-old", "2026-10-22T10:59:59Z"),
        workflow("llama-cpp-build-later", "2026-10-22T11:05:00Z"),
        workflow("llama-cpp-build-frc75", "2026-10-22T11:00:01Z"),
    ]
    assert bf.build_workflow(workflows, PUSHED_AT) == "llama-cpp-build-frc75"


def test_no_workflow_after_the_push_is_none():
    assert bf.build_workflow([workflow("old", "2026-10-22T10:00:00Z")], PUSHED_AT) is None


def test_each_step_is_reported_once_per_phase():
    reported = set()
    running = workflow("w", "2026-10-22T11:00:01Z", nodes={"a": {"displayName": "build-server", "phase": "Running"}})
    assert bf.node_reports(running, reported) == [("info", "  ⚙️  build-server: Running")]
    assert bf.node_reports(running, reported) == []
    failed = workflow("w", "2026-10-22T11:00:01Z", nodes={"a": {"displayName": "build-server", "phase": "Failed"}})
    assert bf.node_reports(failed, reported) == [("error", "  ❌ build-server: Failed")]


def test_a_deploy_is_followed_to_its_registration(follow):
    custom = Custom([workflow("llama-cpp-build-frc75", "2026-10-22T11:00:01Z")], ["Running", "Succeeded"])
    not_found = ApiException(status=404, reason="Not Found")
    logs, recorded, registered = follow(custom, Core([not_found, "an earlier deploy", DEPLOYED_AT]))
    assert recorded == ["llama-cpp-build-frc75"]
    assert registered == ["example.com"]
    assert ("success", "Argo CD applied ConfigMap llama-cpp/thinkube-service-config") in logs


def test_a_recorded_workflow_is_not_looked_for_again(follow):
    custom = Custom([], ["Succeeded"])
    _, recorded, registered = follow(custom, Core([DEPLOYED_AT]), {**BUILD, "workflow": "llama-cpp-build-ws262"})
    assert recorded == []
    assert registered == ["example.com"]


def test_a_failed_build_fails_the_run_and_registers_nothing(follow):
    custom = Custom([], ["Failed"])
    with pytest.raises(bf.BuildFailed, match="llama-cpp-build-x Failed: step build failed"):
        follow(custom, Core([DEPLOYED_AT]), {**BUILD, "workflow": "llama-cpp-build-x"})


def test_a_configmap_argo_cd_never_applies_fails_the_run(follow, monkeypatch):
    monkeypatch.setattr(bf, "CONFIGMAP_SECONDS", 0)
    custom = Custom([], ["Succeeded"])
    with pytest.raises(bf.BuildFailed, match="has not applied ConfigMap llama-cpp/thinkube-service-config"):
        follow(custom, Core(["an earlier deploy"]), {**BUILD, "workflow": "w"})


def test_a_push_that_started_no_build_fails_the_run(follow, monkeypatch):
    monkeypatch.setattr(bf, "WORKFLOW_START_SECONDS", 0)
    with pytest.raises(bf.BuildFailed, match="No build workflow of llama-cpp started"):
        follow(Custom([], ["Running"]), Core([DEPLOYED_AT]))


def test_facts_missing_from_the_run_are_named(follow):
    with pytest.raises(bf.BuildFailed, match="deployed_at"):
        follow(Custom([], ["Running"]), Core([DEPLOYED_AT]), {"pushed_at": PUSHED_AT, "discovery": "a/b"})
