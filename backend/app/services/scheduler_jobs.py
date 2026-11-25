# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""What runs at the Scheduler, as Control reads it at the Scheduler's routes for the parts of the platform."""

from typing import Any, Dict, Optional

import httpx

SCHEDULER_URL = "http://scheduler-backend.thinkube-control.svc.cluster.local:8000/internal/scheduler"


class SchedulerError(Exception):
    """The Scheduler refused or did not answer; the message says which."""


async def running_training() -> Optional[Dict[str, Any]]:
    """A training that runs now: a resumable job of Forge's that holds its GPUs. None when no training runs."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            answer = await client.get(f"{SCHEDULER_URL}/jobs", params={"owner": "forge"})
    except httpx.HTTPError as e:
        raise SchedulerError(f"the Scheduler did not answer: {e}") from e
    if answer.status_code >= 400:
        raise SchedulerError(f"the Scheduler answered {answer.status_code}: {answer.text[:300]}")
    return next((j for j in answer.json()["jobs"] if j.get("resumable") and j["status"] in ("running", "stopping")),
                None)
