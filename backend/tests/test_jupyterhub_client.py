# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Which Hub user the notebook operations act for."""

import asyncio

import pytest

import app.services.jupyterhub_client as hub


def users(monkeypatch, listed):
    async def fake(method, path, json=None, timeout=30.0):
        assert (method, path) == ("GET", "/users")
        return 200, listed

    monkeypatch.setattr(hub, "request", fake)
    monkeypatch.setattr(hub, "_username", None)
    monkeypatch.delenv("JUPYTERHUB_USERNAME", raising=False)


def test_the_user_with_a_server_wins_over_the_admin_listed_first(monkeypatch):
    users(monkeypatch, [
        {"name": "tkadmin", "kind": "user", "admin": True, "servers": {}, "last_activity": None},
        {"name": "thinkube", "kind": "user", "servers": {"": {"ready": True}}, "last_activity": "2026-09-12T15:45:01Z"},
    ])
    assert asyncio.run(hub.username()) == "thinkube"
    assert hub._username == "thinkube"


def test_without_a_server_the_most_recent_sign_in_wins_and_is_not_cached(monkeypatch):
    users(monkeypatch, [
        {"name": "tkadmin", "kind": "user", "admin": True, "servers": {}, "last_activity": None},
        {"name": "thinkube", "kind": "user", "servers": {}, "last_activity": "2026-09-12T15:45:01Z"},
    ])
    assert asyncio.run(hub.username()) == "thinkube"
    assert hub._username is None


def test_nobody_signed_in_is_an_error(monkeypatch):
    users(monkeypatch, [{"name": "tkadmin", "kind": "user", "admin": True, "servers": {}, "last_activity": None}])
    with pytest.raises(hub.HubError):
        asyncio.run(hub.username())


def test_environment_wins(monkeypatch):
    users(monkeypatch, [])
    monkeypatch.setenv("JUPYTERHUB_USERNAME", "someone")
    assert asyncio.run(hub.username()) == "someone"
