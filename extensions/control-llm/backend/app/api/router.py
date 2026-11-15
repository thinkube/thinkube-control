# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/api/router.py
from fastapi import APIRouter
from fastapi import Depends
from app.api import auth, tokens
from app.api.llm.router import llm_router
from app.core.security import get_current_user

api_router = APIRouter()

# Include the routes from the different modules
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# Every route of the gateway needs a sign-in; Control's proxy at llm.<domain> and Control read them at /internal
api_router.include_router(llm_router, prefix="/llm", tags=["llm"], dependencies=[Depends(get_current_user)])
api_router.include_router(tokens.router, prefix="/tokens", tags=["api-tokens"])
