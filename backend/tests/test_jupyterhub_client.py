# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The notebook operations act for the realm user, whom the Hub may not know yet."""

import asyncio

import pytest

import app.services.jupyterhub_client as hub


def fake_hub(monkeypatch, known_users):
    """A Hub that knows `known_users`; returns the list of calls made."""
    calls = []

    async def fake(method, path, json=None, timeout=30.0):
        calls.append((method, path))
        name = path.split("/")[2]
        if method == "GET" and path == f"/users/{name}":
            if name not in known_users:
                raise hub.HubError("JupyterHub answered 404: Not Found", 404)
            return 200, {"name": name, "servers": {}}
        if method == "POST" and path == f"/users/{name}":
            known_users.add(name)
            return 201, {"name": name, "servers": {}}
        if method == "POST" and path.startswith(f"/users/{name}/servers/"):
            return 202, None
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(hub, "request", fake)
    monkeypatch.setenv("JUPYTERHUB_USERNAME", "thinkube")
    return calls


def test_the_user_is_the_configured_realm_user(monkeypatch):
    monkeypatch.setenv("JUPYTERHUB_USERNAME", "thinkube")
    assert asyncio.run(hub.username()) == "thinkube"


def test_without_the_setting_it_is_an_error(monkeypatch):
    monkeypatch.delenv("JUPYTERHUB_USERNAME", raising=False)
    with pytest.raises(hub.HubError, match="JUPYTERHUB_USERNAME"):
        asyncio.run(hub.username())


def test_a_known_user_is_read(monkeypatch):
    calls = fake_hub(monkeypatch, {"thinkube"})
    assert asyncio.run(hub.user())["name"] == "thinkube"
    assert calls == [("GET", "/users/thinkube")]


def test_a_user_who_never_signed_in_is_created(monkeypatch):
    calls = fake_hub(monkeypatch, set())
    assert asyncio.run(hub.user())["name"] == "thinkube"
    assert calls == [("GET", "/users/thinkube"), ("POST", "/users/thinkube")]


def test_starting_a_server_creates_the_user_first(monkeypatch):
    calls = fake_hub(monkeypatch, set())
    assert asyncio.run(hub.start_server("tkamd2", {"node": "tkamd2"})) == 202
    assert calls[-1] == ("POST", "/users/thinkube/servers/tkamd2")
    assert ("POST", "/users/thinkube") in calls


def test_other_hub_errors_are_not_hidden(monkeypatch):
    async def fake(method, path, json=None, timeout=30.0):
        raise hub.HubError("JupyterHub answered 500: boom", 500)

    monkeypatch.setattr(hub, "request", fake)
    monkeypatch.setenv("JUPYTERHUB_USERNAME", "thinkube")
    with pytest.raises(hub.HubError, match="500"):
        asyncio.run(hub.user())
