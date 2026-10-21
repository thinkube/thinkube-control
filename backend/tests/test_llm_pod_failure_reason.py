# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A model pod that crashed is reported with its own termination message.

The vLLM server writes why a load failed (a card short of free memory, a
model missing from MLflow) to /dev/termination-log. The back-off message
Kubernetes adds to a crash loop only says that the container restarts.
"""

from types import SimpleNamespace

import kubernetes

from app.services.llm_pod_manager import LLMPodManager

MODEL = "BSC-LT/salamandraTA-7b-instruct"
SHORT = "GPU 0 (NVIDIA GeForce RTX 3090) has 11.4 GiB free of 24.0 GiB; ..."


def crashed_pod(termination_message):
    terminated = SimpleNamespace(exit_code=1, message=termination_message)
    status = SimpleNamespace(
        state=SimpleNamespace(waiting=SimpleNamespace(
            reason="CrashLoopBackOff",
            message="back-off 10s restarting failed container=vllm")),
        last_state=SimpleNamespace(terminated=terminated),
    )
    return SimpleNamespace(status=SimpleNamespace(container_statuses=[status], phase="Running"))


def deployment(failure):
    conditions = [failure] if failure else []
    return SimpleNamespace(status=SimpleNamespace(conditions=conditions))


def status_of(monkeypatch, pod):
    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "AppsV1Api", lambda: SimpleNamespace(
        read_namespaced_deployment=lambda name, ns: deployment(None)))
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: SimpleNamespace(
        list_namespaced_pod=lambda ns, label_selector: SimpleNamespace(items=[pod])))
    return LLMPodManager().check_pod_status("vllm", "tkamd2", MODEL)


def test_the_termination_message_is_the_reason(monkeypatch):
    assert status_of(monkeypatch, crashed_pod(SHORT + "\n")) == (
        "failed", f"CrashLoopBackOff: {SHORT}")


def test_without_a_termination_message_the_back_off_message_is_kept(monkeypatch):
    assert status_of(monkeypatch, crashed_pod(None)) == (
        "failed", "CrashLoopBackOff: back-off 10s restarting failed container=vllm")


def test_a_pod_kubernetes_refuses_to_create_is_failed_with_the_refusal(monkeypatch):
    """A quota refusal leaves no pod; the Deployment's ReplicaFailure says why."""
    refusal = SimpleNamespace(
        type="ReplicaFailure", status="True", reason="FailedCreate",
        message='pods "flare-x" is forbidden: exceeded quota: resource-budget')
    monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(kubernetes.client, "AppsV1Api", lambda: SimpleNamespace(
        read_namespaced_deployment=lambda name, ns: deployment(refusal)))
    monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: SimpleNamespace(
        list_namespaced_pod=lambda ns, label_selector: SimpleNamespace(items=[])))
    status, detail = LLMPodManager().check_pod_status("vllm", "tkamd2", MODEL)
    assert status == "failed"
    assert detail.startswith("FailedCreate: ") and "exceeded quota" in detail
