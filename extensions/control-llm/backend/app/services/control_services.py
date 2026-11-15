# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Whether a backend is switched on in Thinkube Control's services view, read
from Control's route for its extensions, inside the cluster
(backend/app/api/internal.py of thinkube-control)."""

import httpx

ENABLED_URL = "http://backend:8000/internal/services/enabled"
TIMEOUT = httpx.Timeout(10.0)


class ControlUnavailableError(Exception):
    """Control did not say whether the service is on; the message says why."""


def service_enabled(namespace: str) -> bool:
    """Whether Control has the service of the namespace switched on.

    Raises ControlUnavailableError when Control does not answer.
    """
    try:
        r = httpx.get(ENABLED_URL, params={"namespace": namespace}, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise ControlUnavailableError(
            f"Thinkube Control did not say whether the service in {namespace} is on: {e}"
        ) from e
    if r.status_code != 200:
        raise ControlUnavailableError(
            f"Thinkube Control answered HTTP {r.status_code} for the service in {namespace}: {r.text[:300]}"
        )
    return bool(r.json()["enabled"])
