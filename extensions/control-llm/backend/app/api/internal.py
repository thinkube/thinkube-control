# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The gateway's routes for the other parts of Thinkube Control, at
/internal/llm, outside /api: the extension's address sends only /api to the
backend, and Control's proxy for extensions only /api/v1, so they are reached
only at the Service, inside the cluster. They need no sign-in:

- the LLM proxy at llm.<domain> resolves the model of every request
  (models/resolve, models/resolve-all) and lists the models (models/);
- the Scheduler asks the gateway to give a hold's GPUs up (holds/release).

They are the routes of /api/v1/llm, the same operations.
"""

from fastapi import APIRouter

from app.api.llm.router import llm_router

router = APIRouter()
router.include_router(llm_router, prefix="/internal/llm", include_in_schema=False)
