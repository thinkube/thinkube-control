# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""An agent's access: a Thinkube Control API token signs in, as Control
says whose it is, and the MCP server offers the routes of its TOOLS."""

import httpx
import pytest

from app.core import security
from app.main import mcp
from app.mcp_server import TOOLS

TOKEN = "tk_agent_token"


@pytest.fixture
def control(monkeypatch):
    """Control's /auth/userinfo, answering with `answer` (a status and a body, or an httpx error to raise)."""
    seen = []
    state = {"answer": (200, {"sub": "u-1", "preferred_username": "thinkube", "email": "", "name": "thinkube",
                              "roles": ["api-user"]})}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = state["answer"]
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer[0], json=answer[1])

    real = httpx.AsyncClient
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    state["seen"] = seen
    return state


def test_control_token_signs_in(client, control):
    r = client.get("/api/v1/models/mirrors", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    assert r.json()["jobs"] == []
    asked = control["seen"][0]
    assert str(asked.url) == f"{security.settings.CONTROL_API_URL}/auth/userinfo"
    assert asked.headers["authorization"] == f"Bearer {TOKEN}"


def test_token_control_refuses_is_401(client, control):
    control["answer"] = (401, {"detail": "Not authenticated"})
    r = client.get("/api/v1/models/mirrors", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 401
    assert "does not accept this API token" in r.json()["detail"]


def test_control_unreachable_is_503(client, control):
    control["answer"] = httpx.ConnectError("no route")
    r = client.get("/api/v1/models/mirrors", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 503
    assert "did not answer" in r.json()["detail"]


def test_no_token_is_401(client):
    assert client.get("/api/v1/models/mirrors").status_code == 401
    assert client.get("/api/v1/models/catalog").status_code == 401


def test_mcp_offers_every_tool():
    assert sorted(t.name for t in mcp.tools) == sorted(TOOLS)
