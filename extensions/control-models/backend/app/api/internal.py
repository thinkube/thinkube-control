# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Routes for Thinkube Control's own backend, outside /api: the extension's
address sends only /api to the backend, and Control's proxy only /api/v1, so
these are reached only at the Service, inside the cluster. They need no
sign-in: Control's LLM Gateway reads the catalogue in the background, with no
user's token to pass on."""

from typing import Any, Dict, List

from fastapi import APIRouter

from app.api.models import catalogue_with_state
from app.services.metadata import invalidate_catalogs

router = APIRouter(prefix="/internal")


@router.get("/models")
def internal_models(fresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """The catalogue with is_downloaded and size per model, as models_get_catalog
    answers it. fresh reads the metadata repositories again, not the copy in memory."""
    if fresh:
        invalidate_catalogs()
    return {"models": catalogue_with_state()}
