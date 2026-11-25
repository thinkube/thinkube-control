# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The GPUs of a model's pod, asked of the Scheduler.

The gateway starts its own pods, so it asks the Scheduler for a hold: GPUs kept for the pod. The hold takes its
turn among the work on the node; once it runs, its gpu_ids are the GPUs the pod is started on, named to it in
NVIDIA_VISIBLE_DEVICES with the runtime class GPU_RUNTIME_CLASS, and the pod asks Kubernetes for no GPU. The
Deployment keeps its hold's id (HOLD_ANNOTATION), and the hold ends when the Deployment is deleted.

When work of a higher priority needs the GPUs, the Scheduler asks the gateway to give them up (RELEASE_URL): the
gateway stops the pod, keeping its Deployment at no replicas with RELOAD_ANNOTATION, and releases the hold, which
waits again for its turn; the model is loaded again when the hold has GPUs.

A pod that serves several models (the trained heads, Ollama) has one hold, for the memory of the models on it:
the first model's load asks for it, a further model's load asks the Scheduler to let it grow first (resize), which
the Scheduler refuses when the GPU does not hold the model too, and it shrinks as models leave. Asked to give its
GPUs up, the pod is stopped with the ids of its models kept on its Deployment (MODELS_ANNOTATION), and they are
loaded again when the hold has GPUs.

A hold that no Deployment of the gateway is on, ORPHAN_SECONDS after it was asked for, is ended: a gateway that
stopped between asking for a hold and starting its pod left one.

A load is refused when its hold is not given GPUs within GRANT_SECONDS, with the Scheduler's reason; while the
Scheduler makes room for it, it is waited for up to ROOM_SECONDS.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# The Scheduler's routes for the other parts of the platform, at its Service
SCHEDULER_URL = "http://scheduler-backend:8000/internal/scheduler"
OWNER = "llm-gateway"
# A served model's priority, of 1 (highest) to 5
MODEL_PRIORITY = 4
# Where the Scheduler asks the gateway to give a hold's GPUs up
RELEASE_URL = "http://control-llm-backend:8000/internal/llm/holds/release"
HOLD_ANNOTATION = "scheduler.thinkube.io/hold"
RELOAD_ANNOTATION = "scheduler.thinkube.io/reload"
MODELS_ANNOTATION = "scheduler.thinkube.io/models"
# The runtime class with which NVIDIA_VISIBLE_DEVICES takes a GPU's id
GPU_RUNTIME_CLASS = "nvidia-legacy"
GRANT_SECONDS = 30
ROOM_SECONDS = 900
POLL_SECONDS = 3
MAKING_ROOM = "making room"
ORPHAN_SECONDS = 900


class HoldError(Exception):
    """The Scheduler refused, did not answer, or gave no GPUs; the message says which."""


def _answer(response: httpx.Response, what: str) -> Dict[str, Any]:
    if response.status_code >= 400:
        raise HoldError(f"the Scheduler refused to {what}: {response.status_code} {response.text[:300]}")
    return response.json()


async def _call(method: str, path: str, what: str, body: Optional[dict] = None) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            return _answer(await http.request(method, SCHEDULER_URL + path, json=body), what)
    except httpx.HTTPError as e:
        raise HoldError(f"the Scheduler did not answer ({what}): {e}") from e


async def ask(name: str, node: str, gpus: int, gpu_memory_gb: Optional[float], memory_gb: int = 0,
              on_gpus: Optional[List[str]] = None) -> Dict[str, Any]:
    """A new hold for a model's pod; it waits for its turn."""
    return await _call("POST", "/holds", f"hold GPUs of {node} for {name}", {
        "name": name, "owner": OWNER, "node": node, "gpus": gpus, "gpu_memory_gb": gpu_memory_gb,
        "memory_gb": memory_gb, "priority": MODEL_PRIORITY, "on_gpus": on_gpus, "release_url": RELEASE_URL})


async def get(hold_id: str) -> Dict[str, Any]:
    return await _call("GET", f"/jobs/{hold_id}", f"read the hold {hold_id}")


async def end(hold_id: str) -> None:
    """End a hold: its pod is gone, or will not be started."""
    found = await get(hold_id)
    if found["status"] in ("waiting", "running", "stopping"):
        await _call("POST", f"/jobs/{hold_id}/stop", f"end the hold {hold_id}")


async def release(hold_id: str) -> None:
    """The GPUs the Scheduler asked for are free; the hold waits again for its turn."""
    await _call("POST", f"/jobs/{hold_id}/release", f"release the hold {hold_id}")


async def resize(hold_id: str, gpu_memory_gb: float) -> None:
    """Change the memory a hold takes on each of its GPUs. HoldError, with the Scheduler's reason, when its GPUs
    do not hold the larger amount."""
    await _call("POST", f"/jobs/{hold_id}/memory", f"give the hold {hold_id} {gpu_memory_gb:g} GiB on each GPU",
                {"gpu_memory_gb": gpu_memory_gb})


async def granted(hold_id: str) -> List[str]:
    """The ids of the GPUs the hold was given. HoldError, with the Scheduler's reason, when it is given none in
    time; the hold is ended then."""
    waited, reason = 0, "it has not looked at it yet"
    while True:
        found = await get(hold_id)
        if found["status"] == "running":
            return found["gpu_ids"]
        if found["status"] != "waiting":
            raise HoldError(f"its hold of GPUs is {found['status']}: {found.get('error') or 'ended'}")
        reason = found.get("waiting") or reason
        if waited >= (ROOM_SECONDS if reason.startswith(MAKING_ROOM) else GRANT_SECONDS):
            await end(hold_id)
            raise HoldError(f"the Scheduler gave it no GPU: {reason}")
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS


def end_sync(hold_id: str) -> None:
    """end(), for the code that deletes Deployments outside the event loop."""
    try:
        with httpx.Client(timeout=30.0) as http:
            found = _answer(http.get(f"{SCHEDULER_URL}/jobs/{hold_id}"), f"read the hold {hold_id}")
            if found["status"] in ("waiting", "running", "stopping"):
                _answer(http.post(f"{SCHEDULER_URL}/jobs/{hold_id}/stop"), f"end the hold {hold_id}")
    except httpx.HTTPError as e:
        raise HoldError(f"the Scheduler did not answer (end the hold {hold_id}): {e}") from e


def get_sync(hold_id: str) -> Dict[str, Any]:
    try:
        with httpx.Client(timeout=30.0) as http:
            return _answer(http.get(f"{SCHEDULER_URL}/jobs/{hold_id}"), f"read the hold {hold_id}")
    except httpx.HTTPError as e:
        raise HoldError(f"the Scheduler did not answer (read the hold {hold_id}): {e}") from e


def release_sync(hold_id: str) -> None:
    try:
        with httpx.Client(timeout=30.0) as http:
            _answer(http.post(f"{SCHEDULER_URL}/jobs/{hold_id}/release"), f"release the hold {hold_id}")
    except httpx.HTTPError as e:
        raise HoldError(f"the Scheduler did not answer (release the hold {hold_id}): {e}") from e


def list_sync() -> List[Dict[str, Any]]:
    """The gateway's holds that have not ended."""
    try:
        with httpx.Client(timeout=30.0) as http:
            return _answer(http.get(f"{SCHEDULER_URL}/jobs", params={"owner": OWNER}), "list the gateway's holds")["jobs"]
    except httpx.HTTPError as e:
        raise HoldError(f"the Scheduler did not answer (list the gateway's holds): {e}") from e


def resize_sync(hold_id: str, gpu_memory_gb: float) -> None:
    try:
        with httpx.Client(timeout=30.0) as http:
            _answer(http.post(f"{SCHEDULER_URL}/jobs/{hold_id}/memory", json={"gpu_memory_gb": gpu_memory_gb}),
                    f"give the hold {hold_id} {gpu_memory_gb:g} GiB on each GPU")
    except httpx.HTTPError as e:
        raise HoldError(f"the Scheduler did not answer (resize the hold {hold_id}): {e}") from e
