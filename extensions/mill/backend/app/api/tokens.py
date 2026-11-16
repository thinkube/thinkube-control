# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# app/api/tokens.py
"""API endpoints for managing API tokens."""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user, User
from app.core.api_tokens import (
    APITokenCreate, 
    APITokenResponse, 
    APITokenInfo,
    create_api_token,
    list_api_tokens,
    revoke_api_token,
    get_current_user_dual_auth
)
from app.db.session import get_db

router = APIRouter()

@router.post("/", response_model=APITokenResponse)
async def create_token(
    token_data: APITokenCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new API token for the current user."""
    
    token = await create_api_token(
        db=db,
        user_id=current_user.sub,
        username=current_user.preferred_username,
        name=token_data.name,
        expires_in_days=token_data.expires_in_days,
        scopes=token_data.scopes
    )
    
    return token

@router.get("/", response_model=List[APITokenInfo])
async def list_tokens(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all API tokens for the current user."""
    
    tokens = await list_api_tokens(db, current_user.sub)
    return tokens

@router.delete("/{token_id}")
async def delete_token(
    token_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Revoke an API token."""
    
    success = await revoke_api_token(db, current_user.sub, token_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found"
        )
    
    return {"message": "Token revoked successfully"}

@router.get("/verify")
async def verify_current_token(
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Verify the current token (either Keycloak or API token)."""
    
    return {
        "valid": True,
        "username": current_user.get("preferred_username"),
        "auth_method": current_user.get("auth_method"),
        "token_name": current_user.get("token_name") if current_user.get("auth_method") == "api_token" else None
    }

# 🤖 Generated with Claude