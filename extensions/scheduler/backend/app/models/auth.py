# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/models/auth.py
"""Authentication models."""

from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class AuthInfo(BaseModel):
    """Authentication configuration for frontend."""
    keycloak_url: str
    realm: str
    client_id: str
    auth_url: Optional[str] = None
    token_url: Optional[str] = None
    userinfo_url: Optional[str] = None
    logout_url: Optional[str] = None
    end_session_endpoint: Optional[str] = None


class Token(BaseModel):
    """Token response model."""
    access_token: str
    token_type: str = "bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    refresh_expires_in: Optional[int] = None


class UserInfo(BaseModel):
    """User information from Keycloak."""
    sub: str
    preferred_username: str
    email: Optional[str] = None
    name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    email_verified: Optional[bool] = None
    roles: Optional[List[str]] = None
    realm_access: Optional[Dict[str, List[str]]] = None
    resource_access: Optional[Dict[str, Any]] = None