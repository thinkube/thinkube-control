# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The model catalogue, read from the extension Models (control-models).

The extension owns the catalogue and the mirrors. Control's LLM Gateway and
its template forms read the catalogue with each model's mirror state from
the extension's route for Control, inside the cluster, with no user's token
(extensions/control-models/backend/app/api/internal.py).
"""

from typing import Any, Dict, List

import httpx

from app.services.metadata_fetcher import CatalogUnavailableError

MODELS_URL = "http://control-models-backend:8000/internal/models"
# The extension may read the metadata repositories on GitHub before it answers
TIMEOUT = httpx.Timeout(60.0)


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
