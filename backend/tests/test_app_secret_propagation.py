#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A changed secret value reaches the applications that receive it.

The modules are loaded from their files, with fake Kubernetes clients, so
these run without a cluster and without importing the rest of the application.
"""

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

SERVICES = Path(__file__).resolve().parents[1] / "app/services"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SERVICES / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for package in ("app", "app.services"):
    sys.modules.setdefault(package, types.ModuleType(package))
identity = load("app.services.deploy_identity", "deploy_identity.py")
propagation = load("app_secret_propagation_under_test", "app_secret_propagation.py")


class Cluster:
    """Applications as the API sees them, recording every call."""

    def __init__(self, deployments=None, knative=None, knative_installed=True, missing_secrets=()):
        self.deployments = deployments or {}
        self.knative = knative or {}
        self.knative_installed = knative_installed
        self.missing_secrets = set(missing_secrets)
        self.calls = []

    # CoreV1Api
    def patch_namespaced_secret(self, name, namespace, body, _content_type):
        self.calls.append(("secret", namespace, name, body, _content_type))
        if namespace in self.missing_secrets:
            raise ApiException(status=404, reason="Not Found")

    # AppsV1Api
    def list_namespaced_deployment(self, namespace, label_selector):
        assert label_selector == f"app.kubernetes.io/name={namespace}"
        names = self.deployments.get(namespace, [])
        return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name=n)) for n in names])

    def patch_namespaced_deployment(self, name, namespace, body, _content_type):
        self.calls.append(("deployment", namespace, name, body, _content_type))

    # CustomObjectsApi
    def list_namespaced_custom_object(self, group, version, namespace, plural):
        if not self.knative_installed:
            raise ApiException(status=404, reason="Not Found")
        return {"items": [{"metadata": {"name": n}} for n in self.knative.get(namespace, [])]}

    def patch_namespaced_custom_object(self, group, version, namespace, plural, name, body, _content_type):
        self.calls.append(("ksvc", namespace, name, body, _content_type))


@pytest.fixture
def cluster(monkeypatch):
    holder = {}

    def use(c):
        holder["c"] = c
        monkeypatch.setattr(propagation, "client", SimpleNamespace(
            CoreV1Api=lambda api: c, AppsV1Api=lambda api: c, CustomObjectsApi=lambda api: c,
        ))
        return c

    return use


def restarted_at(body):
    return body["spec"]["template"]["metadata"]["annotations"]["kubectl.kubernetes.io/restartedAt"]


def test_the_value_is_written_and_every_deployment_restarts(cluster):
    c = cluster(Cluster(deployments={"shop": ["shop-backend", "shop-frontend"]}))

    restarted, failed = propagation.propagate("API_KEY", "new", ["shop"], api_client=None)

    assert (restarted, failed) == (["shop"], {})
    assert ("secret", "shop", "shop-secrets", {"stringData": {"API_KEY": "new"}}, identity.MERGE_PATCH) in c.calls
    patched = [call for call in c.calls if call[0] == "deployment"]
    assert [call[2] for call in patched] == ["shop-backend", "shop-frontend"]
    assert all(restarted_at(call[3]) for call in patched)


def test_every_patch_is_a_merge_patch(cluster):
    c = cluster(Cluster(deployments={"shop": ["shop-backend"]}, knative={"fn": ["fn"]}))
    propagation.propagate("API_KEY", "new", ["shop", "fn"], api_client=None)
    assert {call[4] for call in c.calls} == {"application/merge-patch+json"}


def test_a_knative_app_gets_a_new_revision_and_its_deployments_are_left_to_knative(cluster):
    c = cluster(Cluster(deployments={"fn": ["fn-00001-deployment"]}, knative={"fn": ["fn"]}))

    restarted, failed = propagation.propagate("API_KEY", "new", ["fn"], api_client=None)

    assert restarted == ["fn"]
    assert [call[:3] for call in c.calls if call[0] != "secret"] == [("ksvc", "fn", "fn")]


def test_without_knative_installed_deployments_are_restarted(cluster):
    c = cluster(Cluster(deployments={"shop": ["shop-backend"]}, knative_installed=False))
    restarted, failed = propagation.propagate("API_KEY", "new", ["shop"], api_client=None)
    assert restarted == ["shop"]
    assert [call[2] for call in c.calls if call[0] == "deployment"] == ["shop-backend"]


def test_one_app_failing_does_not_stop_the_others_and_says_why(cluster):
    cluster(Cluster(deployments={"shop": ["shop-backend"], "blog": ["blog-web"]}, missing_secrets={"blog"}))

    restarted, failed = propagation.propagate("API_KEY", "new", ["blog", "shop"], api_client=None)

    assert restarted == ["shop"]
    assert failed == {"blog": "404 Not Found"}


def test_an_app_with_nothing_to_restart_is_reported(cluster):
    cluster(Cluster())
    restarted, failed = propagation.propagate("API_KEY", "new", ["ghost"], api_client=None)
    assert restarted == []
    assert "no Deployment labelled app.kubernetes.io/name=ghost" in failed["ghost"]


def test_writes_need_the_deploy_kubeconfig(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "DEPLOY_KUBECONFIG", tmp_path / "missing" / "config")
    with pytest.raises(RuntimeError, match="kubeconfig the first deploy uses"):
        identity.deploy_api_client()
