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
    get_current_user_dual_auth,
)
from app.db.session import get_db

router = APIRouter()


@router.post("/", response_model=APITokenResponse)
async def create_token(
    token_data: APITokenCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new API token for the current user."""

    token = await create_api_token(
        db=db,
        user_id=current_user.preferred_username,
        username=current_user.preferred_username,
        name=token_data.name,
        expires_in_days=token_data.expires_in_days,
        scopes=token_data.scopes,
    )

    return token


@router.get("/", response_model=List[APITokenInfo])
async def list_tokens(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """List all API tokens - simplified for single-user homelab."""
    # This is a homelab - just show all tokens to the authenticated user
    from app.core.api_tokens import APIToken, APITokenInfo

    tokens = db.query(APIToken).order_by(APIToken.created_at.desc()).all()

    return [
        APITokenInfo(
            id=token.id,
            name=token.name,
            created_at=token.created_at,
            expires_at=token.expires_at,
            last_used=token.last_used,
            is_active=token.is_active,
            scopes=token.scopes,
        )
        for token in tokens
    ]


@router.delete("/{token_id}")
async def delete_token(
    token_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke an API token - simplified for single-user homelab."""
    from app.core.api_tokens import APIToken

    # Just delete the token - this is a homelab with one user
    token = db.query(APIToken).filter(APIToken.id == token_id).first()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )

    token.is_active = False
    db.commit()
    return {"message": "Token revoked successfully"}


@router.get("/{token_id}/reveal")
async def reveal_token(
    token_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reveal the actual token value for system-generated tokens stored in K8s secrets.

    This is specifically for homelab convenience - allows retrieving system tokens
    like the CI/CD monitoring token that are stored in Kubernetes secrets.
    """
    from app.core.api_tokens import APIToken
    from kubernetes import client, config
    import base64

    # Get the token from database
    token = db.query(APIToken).filter(APIToken.id == token_id).first()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )

    # Only reveal system tokens (stored in K8s secrets)
    if token.name not in ["CI/CD Monitoring", "MCP Default"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only system-generated tokens can be revealed"
        )

    try:
        # Load Kubernetes config
        config.load_incluster_config()
        v1 = client.CoreV1Api()

        # Determine secret name based on token name
        secret_name = "cicd-monitoring-token" if token.name == "CI/CD Monitoring" else "mcp-default-token"

        # Get the secret containing the token
        secret = v1.read_namespaced_secret(
            name=secret_name,
            namespace="thinkube-control"
        )

        # Extract and decode the token
        token_value = base64.b64decode(secret.data["token"]).decode("utf-8")

        return {
            "id": token.id,
            "name": token.name,
            "token": token_value,
            "message": "This is your actual token value. Store it securely."
        }

    except Exception as e:
        # If we can't access K8s secrets, provide helpful error
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to retrieve token from Kubernetes: {str(e)}"
        )


@router.get("/verify")
async def verify_current_token(
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Verify the current token (either Keycloak or API token)."""

    return {
        "valid": True,
        "username": current_user.get("preferred_username"),
        "auth_method": current_user.get("auth_method"),
        "token_name": (
            current_user.get("token_name")
            if current_user.get("auth_method") == "api_token"
            else None
        ),
    }


# 🤖 Generated with Claude
