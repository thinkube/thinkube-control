# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The owners of the holds: a service that starts its own pods on the GPUs a hold gave it, and is asked to give
them up when work of a higher priority needs them."""

from typing import Any, Dict

import httpx


class OwnerError(Exception):
    """The owner could not be asked; the message says why."""


class Owners:
    async def ask_release(self, url: str, payload: Dict[str, Any]) -> None:
        """Ask a hold's owner to free the hold's GPUs: a POST to the address the hold names, with the hold's id,
        its name and the job that needs the GPUs. The owner answers at once, frees them (stops its pod), and then
        tells the scheduler they are free (the hold's release). Asked again while it does, it answers the same."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                answer = await http.post(url, json=payload)
        except httpx.HTTPError as e:
            raise OwnerError(f"{url} did not answer: {e}") from e
        if answer.status_code >= 400:
            raise OwnerError(f"{url} answered {answer.status_code}: {answer.text[:300]}")
