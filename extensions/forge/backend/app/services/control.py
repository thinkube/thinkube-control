# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Calls to Thinkube Control's API. A request of a person carries that
person's own Keycloak token, so Control applies their rights; the queue
worker calls with the Control API token a person gave it
(app/models/forge.py, WorkerCredential)."""

from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

TIMEOUT = httpx.Timeout(60.0)

# The extension Models (control-models), at its Service in Control's namespace: the model catalogue
MODELS_API_URL = "http://control-models-backend:8000/api/v1"


class ControlError(Exception):
    """Thinkube Control refused or failed a call; `status` is its HTTP status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def call(token: str, method: str, path: str, body: Optional[Dict[str, Any]] = None,
               params: Optional[Dict[str, Any]] = None, base: Optional[str] = None) -> Any:
    """A call to Control's API, or to base: the API of an extension of Control."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.request(method, f"{base or settings.CONTROL_API_URL}{path}", json=body, params=params,
                                     headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise ControlError(502, f"Thinkube Control did not answer {method} {path}: {e}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ControlError(r.status_code, f"Thinkube Control answered {method} {path} with {r.status_code}: {detail}")
    return r.json()


async def catalog(token: str) -> Dict[str, Any]:
    return await call(token, "GET", "/models/catalog", base=MODELS_API_URL)


async def nodes_choices(token: str) -> Dict[str, Dict[str, list]]:
    """By node, the values Control accepts for a notebook job on it, per resource: "memory_gb" in GiB,
    "cpu_cores", "gpus"."""
    return {n["node"]: n["choices"] for n in (await call(token, "GET", "/jupyterhub/config"))["nodes"]}


async def node_choices(token: str, node: str) -> Dict[str, list]:
    """The values Control accepts for a notebook job on the node, per resource: "memory_gb" in GiB, "cpu_cores"."""
    found = (await nodes_choices(token)).get(node)
    if found is None:
        raise ControlError(404, f"Thinkube Control lists no node {node} in /jupyterhub/config")
    return found


async def gpu_status(token: str) -> Dict[str, Any]:
    return await call(token, "GET", "/llm/gpu/status/")


async def run_job(token: str, request: Dict[str, Any]) -> Dict[str, Any]:
    return (await call(token, "POST", "/jupyter/jobs", request))["job"]


async def job(token: str, job_id: str) -> Dict[str, Any]:
    return (await call(token, "GET", f"/jupyter/jobs/{job_id}"))["job"]


async def cancel_job(token: str, job_id: str) -> Dict[str, Any]:
    return (await call(token, "POST", f"/jupyter/jobs/{job_id}/cancel"))["job"]


async def training_mode(token: str) -> Dict[str, Any]:
    """{"on": bool, "record": {"node", "reason", ...} or None}. Control reserves one node at a time."""
    return await call(token, "GET", "/training-mode")


async def training_mode_on(token: str, node: str, reason: str) -> Dict[str, Any]:
    return await call(token, "POST", "/training-mode/on", {"node": node, "reason": reason})


async def training_mode_off(token: str) -> Dict[str, Any]:
    return await call(token, "POST", "/training-mode/off")


async def notebook_servers(token: str) -> list:
    """The person's notebook server of each node: node, server_name, state (running, starting, stopping, stopped)."""
    return (await call(token, "GET", "/jupyter/servers"))["servers"]


async def stop_notebook_server(token: str, node: str) -> Dict[str, Any]:
    return await call(token, "POST", f"/jupyter/servers/{node}/stop")


async def jobs(token: str) -> list:
    """The newest notebook jobs, each with its node and status."""
    return await call(token, "GET", "/jupyter/jobs")


async def services(token: str) -> list:
    """The platform's services, each with its namespace and the nodes it holds GPUs on (gpu_nodes)."""
    return (await call(token, "GET", "/services/"))["services"]


async def venvs(token: str) -> list:
    """The Python environments of the notebook servers, each with its name and build status."""
    return (await call(token, "GET", "/jupyter-venvs"))["venvs"]


async def create_api_token(token: str, name: str) -> Dict[str, Any]:
    """A Control API token of the signed-in user: {"id", "name", "token", ...}; the token is shown only here."""
    return await call(token, "POST", "/tokens/", {"name": name})


async def revoke_api_token(token: str, token_id: str) -> Dict[str, Any]:
    return await call(token, "DELETE", f"/tokens/{token_id}")


async def model_status(token: str, model_id: str) -> Dict[str, Any]:
    return await call(token, "GET", f"/llm/models/{model_id}/status")


async def load_model(token: str, model_id: str, node: Optional[str]) -> Dict[str, Any]:
    return await call(token, "POST", f"/llm/models/{model_id}/load", {"node": node} if node else {})


async def unload_model(token: str, model_id: str) -> Dict[str, Any]:
    return await call(token, "POST", f"/llm/models/{model_id}/unload")
