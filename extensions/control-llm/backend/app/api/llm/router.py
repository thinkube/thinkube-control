# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

from fastapi import APIRouter

from app.api.llm import models_api, backends_api, gpu_api, holds_api, refresh_api, lifecycle_api

llm_router = APIRouter()

llm_router.include_router(models_api.router, prefix="/models", tags=["llm-models"])
llm_router.include_router(backends_api.router, prefix="/backends", tags=["llm-backends"])
llm_router.include_router(gpu_api.router, prefix="/gpu/status", tags=["llm-gpu"])
llm_router.include_router(refresh_api.router, prefix="/refresh", tags=["llm-refresh"])
llm_router.include_router(lifecycle_api.router, prefix="/models", tags=["llm-lifecycle"])
# What the Scheduler asks of the gateway; no tool and no page calls it
llm_router.include_router(holds_api.router, prefix="/holds", include_in_schema=False)
