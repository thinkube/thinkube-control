# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The model catalogue, read from the extension Models (control-models).

That extension owns the catalogue and the mirrors. The gateway's registry reads
the catalogue with each model's mirror state from its route for the other
parts of Control, inside the cluster, with no user's token
(extensions/control-models/backend/app/api/internal.py).
"""

from typing import Any, Dict, List

import httpx

MODELS_URL = "http://control-models-backend:8000/internal/models"
# The extension may read the metadata repositories on GitHub before it answers
TIMEOUT = httpx.Timeout(60.0)


class CatalogUnavailableError(Exception):
    """The extension Models did not give the catalogue; the message says why."""


def catalogue(fresh: bool = False) -> List[Dict[str, Any]]:
    """Every catalogue model with is_downloaded and size. fresh makes the
    extension read the metadata repositories again.

    Raises CatalogUnavailableError naming the extension's answer when it
    cannot give the catalogue.
    """
    try:
        r = httpx.get(MODELS_URL, params={"fresh": "true"} if fresh else None, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise CatalogUnavailableError(
            f"The extension Models did not answer at {MODELS_URL}: {e}. Is control-models deployed?"
        ) from e
    if r.status_code != 200:
        raise CatalogUnavailableError(
            f"The extension Models answered HTTP {r.status_code} at {MODELS_URL}: {r.text[:300]}"
        )
    return r.json()["models"]
