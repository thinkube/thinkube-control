# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A small client for the JupyterHub REST API.

thinkube-control is registered with the Hub as the service ``thinkube-control``
with admin rights; its token arrives in ``JUPYTERHUB_SERVICE_TOKEN``. The Hub
is reached inside the cluster on its API port. The platform has one notebook
user; the client finds that user through the Hub rather than through
configuration, so nothing else has to name it.
"""

from __future__ import annotations

import asyncio
import json
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


async def username() -> str:
    """The notebook user's name: the platform's realm user, set by the deploy."""
    name = os.environ.get("JUPYTERHUB_USERNAME", "").strip()
    if not name:
        raise HubError("JUPYTERHUB_USERNAME is not set; the thinkube-control deploy sets it to the realm user")
    return name


async def user(include_stopped: bool = False) -> Dict[str, Any]:
    """The notebook user's Hub model. The Hub creates a user at its first
    sign-in; when the realm user has not signed in yet, it is created here so
    servers can start without a Hub login first.

    The Hub lists only running and starting servers unless asked for the
    stopped ones too (include_stopped_servers); a stopped named server still
    counts toward the Hub's limit of named servers."""
    name = await username()
    query = "?include_stopped_servers=true" if include_stopped else ""
    try:
        _, model = await request("GET", f"/users/{name}{query}")
    except HubError as e:
        if e.status != 404:
            raise
        _, model = await request("POST", f"/users/{name}")
    return model


def server_path(name: str) -> str:
    """The Hub API path of a named server; the platform uses named servers only."""
    if not name:
        raise HubError("a notebook server name is required; the Hub's default server is not used")
    return f"servers/{name}"


async def server(name: str, include_stopped: bool = False) -> Optional[Dict[str, Any]]:
    """The Hub's model of one server, or None when it is not listed: when it
    does not exist, or, without include_stopped, when it is stopped."""
    return (await user(include_stopped)).get("servers", {}).get(name)


async def start_failure(name: str) -> Optional[str]:
    """Why the Hub's last start of a server failed, in the Hub's words: the message of the failed event in the
    server's progress, such as the Scheduler's reason for giving it no GPU. None when the Hub has no failed start
    of it to tell."""
    url = f"{hub_api_url()}/users/{await username()}/{server_path(name)}/progress"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream("GET", url, headers={"Authorization": f"token {_token()}"}) as response:
                if response.status_code != 200:
                    return None
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[len("data:"):])
                    if event.get("failed"):
                        return event.get("message")
                    if event.get("ready"):
                        return None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("the Hub's progress of the server %s could not be read: %s", name, e)
    return None


async def start_server(name: str, user_options: Dict[str, Any]) -> int:
    """Ask the Hub to start a server. 201 means started, 202 means still starting. A start that fails while the
    Hub still answers is raised with the Hub's reason for it."""
    owner = (await user())["name"]
    try:
        status, _ = await request("POST", f"/users/{owner}/{server_path(name)}", json=user_options, timeout=60)
    except HubError as e:
        reason = await start_failure(name) if e.status is not None and e.status >= 500 else None
        if reason is None:
            raise
        raise HubError(f"the server '{name}' failed to start: {reason}", e.status) from e
    return status


async def stop_server(name: str) -> None:
    """Ask the Hub to stop a server; a server that is not running is not an error."""
    try:
        await request("DELETE", f"/users/{await username()}/{server_path(name)}", timeout=60)
    except HubError as e:
        if e.status not in (400, 404):
            raise


async def remove_server(name: str, timeout: float = 120.0, interval: float = 2.0) -> None:
    """Stop a server and delete it from the Hub, so it no longer counts toward
    the Hub's limit of named servers. A server that does not exist is not an
    error. Raises HubError when it is still listed after the wait."""
    try:
        await request("DELETE", f"/users/{await username()}/{server_path(name)}", json={"remove": True}, timeout=60)
    except HubError as e:
        if e.status != 404:
            raise
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while await server(name, include_stopped=True) is not None:
        if loop.time() > deadline:
            raise HubError(f"the server '{name}' was still listed {int(timeout)} seconds after it was removed")
        await asyncio.sleep(interval)


async def wait_ready(name: str, timeout: float = 240.0, interval: float = 3.0) -> Dict[str, Any]:
    """Poll until the server is ready. Raises HubError when it stops or the wait runs out."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        model = await server(name)
        if model is None:
            reason = await start_failure(name)
            raise HubError(f"the server '{name}' failed to start: {reason}" if reason
                           else f"the server '{name}' stopped before it was ready; see the Hub's log")
        if model.get("ready"):
            return model
        if not model.get("pending"):
            reason = await start_failure(name)
            raise HubError(f"the server '{name}' failed to start: {reason or model.get('state') or 'no reason given'}")
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
