"""A small client for the JupyterHub REST API.

thinkube-control is registered with the Hub as the service ``thinkube-control``
with admin rights; its token arrives in ``JUPYTERHUB_SERVICE_TOKEN``. The Hub
is reached inside the cluster on its API port. The platform has one notebook
user; the client finds that user through the Hub rather than through
configuration, so nothing else has to name it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_HUB_API_URL = "http://hub.jupyterhub.svc.cluster.local:8081/hub/api"


class HubError(Exception):
    """The Hub refused or could not be reached; ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def hub_api_url() -> str:
    return os.environ.get("JUPYTERHUB_API_URL", DEFAULT_HUB_API_URL).rstrip("/")


def _token() -> str:
    token = os.environ.get("JUPYTERHUB_SERVICE_TOKEN")
    if not token:
        raise HubError("thinkube-control has no JupyterHub service token; redeploy thinkube-control after JupyterHub")
    return token


async def request(method: str, path: str, json: Any = None, timeout: float = 30.0) -> tuple[int, Any]:
    url = f"{hub_api_url()}/{path.lstrip('/')}"
    headers = {"Authorization": f"token {_token()}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, json=json)
    except httpx.HTTPError as e:
        raise HubError(f"JupyterHub is not reachable at {hub_api_url()}: {e}") from e
    body: Any = None
    if response.content:
        try:
            body = response.json()
        except ValueError:
            body = response.text
    if response.status_code >= 400:
        detail = body.get("message") if isinstance(body, dict) else body
        raise HubError(f"JupyterHub answered {response.status_code}: {detail}", response.status_code)
    return response.status_code, body


_username: Optional[str] = None


async def username() -> str:
    """The notebook user's name.

    From the environment when set. Otherwise the Hub's users are asked: the
    one with a server wins, then the one that signed in most recently. The
    Hub also lists the admin user from its configuration, who may never have
    signed in, so "the first user" is not enough.
    """
    global _username
    if _username:
        return _username
    configured = os.environ.get("JUPYTERHUB_USERNAME")
    if configured:
        _username = configured
        return configured
    _, users = await request("GET", "/users")
    real = [u for u in users if u.get("kind", "user") == "user"]
    if not real:
        raise HubError("JupyterHub has no user yet; sign in to Thinkube Notebooks once")
    with_server = [u for u in real if u.get("servers")]
    if with_server:
        _username = with_server[0]["name"]
        return _username
    signed_in = [u for u in real if u.get("last_activity")]
    if not signed_in:
        raise HubError("nobody has signed in to Thinkube Notebooks yet; sign in once so the Hub knows the user")
    # A guess from activity is not cached; a live server settles it later.
    return max(signed_in, key=lambda u: u["last_activity"])["name"]


async def user() -> Dict[str, Any]:
    _, model = await request("GET", f"/users/{await username()}")
    return model


def server_path(name: str) -> str:
    """The Hub API path of a named server; the platform uses named servers only."""
    if not name:
        raise HubError("a notebook server name is required; the Hub's default server is not used")
    return f"servers/{name}"


async def server(name: str) -> Optional[Dict[str, Any]]:
    """The Hub's model of one server, or None when it does not exist."""
    return (await user()).get("servers", {}).get(name)


async def start_server(name: str, user_options: Dict[str, Any]) -> int:
    """Ask the Hub to start a server. 201 means started, 202 means still starting."""
    status, _ = await request("POST", f"/users/{await username()}/{server_path(name)}", json=user_options, timeout=60)
    return status


async def stop_server(name: str) -> None:
    """Ask the Hub to stop a server; a server that is not running is not an error."""
    try:
        await request("DELETE", f"/users/{await username()}/{server_path(name)}", timeout=60)
    except HubError as e:
        if e.status not in (400, 404):
            raise


async def wait_ready(name: str, timeout: float = 240.0, interval: float = 3.0) -> Dict[str, Any]:
    """Poll until the server is ready. Raises HubError when it stops or the wait runs out."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        model = await server(name)
        if model is None:
            raise HubError(f"the server '{name}' stopped before it was ready; see the Hub's log")
        if model.get("ready"):
            return model
        if not model.get("pending"):
            raise HubError(f"the server '{name}' failed to start: {model.get('state') or 'no reason given'}")
        if loop.time() > deadline:
            raise HubError(f"the server '{name}' did not become ready in {int(timeout)} seconds")
        await asyncio.sleep(interval)


async def wait_stopped(name: str, timeout: float = 90.0, interval: float = 2.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        model = await server(name)
        if model is None or (not model.get("ready") and not model.get("pending")):
            return
        await asyncio.sleep(interval)
    raise HubError(f"the server '{name}' did not stop in {int(timeout)} seconds")
