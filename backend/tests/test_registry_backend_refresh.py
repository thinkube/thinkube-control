#!/usr/bin/env python3
"""Tests that the registry re-probes backends before reconciling model states.

_reconcile_states decides which models are serving by reading the discovered
backend list. Nothing refreshed that list on a schedule, so a model that had
finished loading stayed 'loading' until someone refreshed the registry by hand.
"""

import asyncio

import pytest

from app.services.llm_model_registry import LLMModelRegistry


@pytest.fixture
def registry():
    return LLMModelRegistry.__new__(LLMModelRegistry)


@pytest.mark.asyncio
async def test_refresh_calls_backend_discovery(registry, monkeypatch):
    calls = []

    async def refresh():
        calls.append(1)
        return 1

    import app.services.llm_backend_discovery as disc

    monkeypatch.setattr(disc.llm_backend_discovery, "refresh", refresh)

    await registry._refresh_backends()

    assert calls == [1]


@pytest.mark.asyncio
async def test_a_probe_failure_does_not_stop_the_loop(registry, monkeypatch):
    async def boom():
        raise RuntimeError("unreachable")

    import app.services.llm_backend_discovery as disc

    monkeypatch.setattr(disc.llm_backend_discovery, "refresh", boom)

    await registry._refresh_backends()  # must not raise


@pytest.mark.asyncio
async def test_polling_refreshes_backends_before_reconciling(monkeypatch):
    """Order matters: reconciling against a stale list is the whole bug."""
    order = []

    r = LLMModelRegistry.__new__(LLMModelRegistry)
    r._is_running = False
    r._refresh_interval = 0

    def reconcile():
        order.append("reconcile")
        r._is_running = False  # end the loop after one pass

    async def fake_refresh():
        order.append("backends")

    real_sleep = asyncio.sleep

    async def no_wait(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(r, "_poll_catalog", lambda: order.append("catalog"))
    monkeypatch.setattr(r, "_reconcile_states", reconcile)
    monkeypatch.setattr(r, "_refresh_backends", fake_refresh)
    monkeypatch.setattr(asyncio, "sleep", no_wait)

    await r.start_polling()

    assert order == ["catalog", "backends", "reconcile"]
