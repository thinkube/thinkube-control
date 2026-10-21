# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A restart of Thinkube Control never restarts the loaded models.

Reconciliation at start reads the gateway-managed deployments and patches
none of them; a newer backend image reaches a deployment at its next load.
"""

from types import SimpleNamespace

import pytest

from app.services.llm_pod_manager import LLMPodManager, MODEL_LABEL, TARGET_NODE_LABEL


def _deployment(name, node, slug):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels={TARGET_NODE_LABEL: node, MODEL_LABEL: slug}),
        spec=SimpleNamespace(template=SimpleNamespace(spec=SimpleNamespace(containers=[
            SimpleNamespace(name="inference", image="registry/vllm-inference:old")]))),
    )


@pytest.fixture
def apps(monkeypatch):
    import kubernetes

    patched = []
    deploys = [_deployment("vllm-inference-tkspark-m", "tkspark", "m")]

    class Apps:
        def list_namespaced_deployment(self, ns, label_selector):
            return SimpleNamespace(items=deploys if ns == "vllm" else [])

        def read_namespaced_deployment(self, name, ns):
            return SimpleNamespace(spec=SimpleNamespace(template=SimpleNamespace(spec=SimpleNamespace(
                containers=[SimpleNamespace(name="inference", image="registry/vllm-inference:new")]))))

        def patch_namespaced_deployment(self, *a, **k):
            patched.append(a)

    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "AppsV1Api", Apps)
    return patched


def test_reconcile_at_start_patches_no_deployment(apps, monkeypatch):
    mgr = LLMPodManager()
    monkeypatch.setattr(mgr, "_find_pod_for_deployment", lambda ns, sel: ("10.0.0.1", "pod", True))
    pods = mgr._reconcile_sync()
    assert [p.deployment_name for p in pods] == ["vllm-inference-tkspark-m"]
    assert apps == []
