# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/api/router.py
from fastapi import APIRouter
from app.api import auth, custom_images, images, tokens

api_router = APIRouter()

# Include the routes from the different modules
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(images.router, tags=["images"])
api_router.include_router(custom_images.router, tags=["custom-images"])
api_router.include_router(tokens.router, prefix="/tokens", tags=["api-tokens"])
