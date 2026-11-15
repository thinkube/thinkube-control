# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Calls to the LLM Gateway, the extension control-llm, at its routes for the
other parts of Control (/internal/llm, reached only inside the cluster).

Training mode uses them: it unloads a node's models when it turns on, and
loads them again, one after another, for up to 45 minutes after the request
that turned it off, when no user's token is still valid.
"""

from typing import Any, Dict, List, Optional

import httpx

GATEWAY_URL = "http://control-llm-backend:8000/internal/llm"
# A load or an unload answers once the gateway has accepted or refused it
TIMEOUT = httpx.Timeout(120.0)


class GatewayError(Exception):
    """The LLM Gateway did not answer, or refused the call; the message says which."""


async def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            r = await http.request(method, f"{GATEWAY_URL}{path}", json=body)
    except httpx.HTTPError as e:
        raise GatewayError(f"The LLM Gateway (control-llm) did not answer {method} {path}: {e}") from e
    if r.status_code >= 400:
        raise GatewayError(f"The LLM Gateway answered {method} {path} with HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


async def allocations(node: str) -> List[Dict[str, str]]:
    """The served models allocated on the node, each as {model_id, node}."""
    status = await _call("GET", "/gpu/status/")
    return [{"model_id": a["model_id"], "node": n["name"]}
            for n in status["nodes"] if n["name"] == node for a in n["allocations"]]


async def unload(model_id: str) -> Dict[str, Any]:
    """Unload the model; the answer's state is "deployable" when it was unloaded."""
    return await _call("POST", f"/models/{model_id}/unload", {"force": False})


async def load(model_id: str, node: str) -> Dict[str, Any]:
    """Load the model on the node; the answer's state is "loading" when the load was accepted."""
    return await _call("POST", f"/models/{model_id}/load", {"node": node})


async def state(model_id: str) -> str:
    """The model's state in the gateway: deployable, loading, available..."""
    return (await _call("GET", f"/models/{model_id}/status"))["model"]["state"]
