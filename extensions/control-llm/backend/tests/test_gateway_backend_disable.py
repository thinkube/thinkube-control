# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Placement honours a backend switched off in Control's services view, and a
Control that does not answer stops the placement with its reason."""

import httpx
import pytest

from app.services import control_services
from app.services.control_services import ControlUnavailableError
from app.services.llm_pod_manager import LLMPodManager


def control_answering(monkeypatch, answer):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append((url, params))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(control_services.httpx, "get", get)
    return seen


def test_control_is_asked_for_the_backends_namespace(monkeypatch):
    seen = control_answering(monkeypatch, httpx.Response(200, json={"namespace": "vllm", "enabled": False}))
    assert LLMPodManager()._is_disabled("vllm") is True
    assert seen == [("http://backend:8000/internal/services/enabled", {"namespace": "vllm"})]


def test_a_switched_on_backend_is_not_disabled(monkeypatch):
    control_answering(monkeypatch, httpx.Response(200, json={"namespace": "ollama", "enabled": True}))
    assert LLMPodManager()._is_disabled("ollama") is False


def test_a_control_that_does_not_answer_is_named(monkeypatch):
    control_answering(monkeypatch, httpx.ConnectError("connection refused"))
    with pytest.raises(ControlUnavailableError, match="did not say whether the service in vllm is on"):
        LLMPodManager()._is_disabled("vllm")


@pytest.mark.asyncio
async def test_placement_is_refused_while_the_backend_is_disabled(monkeypatch):
    pods = LLMPodManager()
    monkeypatch.setattr(pods, "_is_disabled", lambda backend_type: True)

    ok, managed = await pods.ensure_pod("vllm", "tkamd2")

    assert ok is False
    assert managed is None
