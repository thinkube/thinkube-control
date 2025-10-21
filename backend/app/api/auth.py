# app/api/auth.py
import logging
from typing import Dict
from fastapi import APIRouter, Depends, HTTPException, status, Request
from app.core.config import settings
from app.models.dashboards import UserInfo, AuthInfo, Token
from app.core.security import get_current_active_user, exchange_code_for_token, User
from app.core.api_tokens import get_current_user_dual_auth

router = APIRouter()

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@router.get("/auth-config", response_model=AuthInfo)
async def get_auth_config():
    """Return authentication configuration for frontend."""
    return AuthInfo(
        keycloak_url=settings.KEYCLOAK_URL,
        realm=settings.KEYCLOAK_REALM,
        client_id=settings.KEYCLOAK_CLIENT_ID,
        auth_url=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/auth",
        token_url=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token",
        userinfo_url=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/userinfo",
        logout_url=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout",
        end_session_endpoint=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout",
    )


@router.get("/userinfo", response_model=UserInfo, operation_id="get_user_info")
async def get_userinfo(current_user = Depends(get_current_user_dual_auth)):
    """Get current user information - supports both Keycloak and API token auth."""
    # Handle both User object (Keycloak) and dict (API token)
    if hasattr(current_user, 'sub'):
        # Keycloak User object
        return UserInfo(
            sub=current_user.sub,
            preferred_username=current_user.preferred_username,
            email=current_user.email,
            name=current_user.name,
            roles=current_user.realm_access.get("roles", []),
        )
    else:
        # API token dict
        return UserInfo(
            sub=current_user.get("sub", "api-user"),
            preferred_username=current_user.get("preferred_username", current_user.get("token_name", "api-user")),
            email=current_user.get("email", ""),
            name=current_user.get("name", current_user.get("token_name", "API User")),
            roles=current_user.get("realm_access", {}).get("roles", ["api-user"]),
        )


from pydantic import BaseModel


class TokenRequest(BaseModel):
    code: str
    redirect_uri: str


@router.post("/token", response_model=Token)
async def get_token(request: TokenRequest):
    """Exchange authorization code for access token."""
    try:
        token_response = await exchange_code_for_token(
            request.code, request.redirect_uri
        )
        return Token(
            access_token=token_response.get("access_token"),
            token_type="Bearer",
            expires_in=token_response.get("expires_in", 3600),
            refresh_token=token_response.get("refresh_token"),
            refresh_expires_in=token_response.get("refresh_expires_in", 86400),
        )
    except Exception as e:
        logger.error(f"Token exchange error: {e}")
        raise HTTPException(
            status_code=400, detail="Failed to exchange authorization code"
        )


class RefreshTokenRequest(BaseModel):
    refresh_token: str


@router.post("/refresh-token", response_model=Token)
async def refresh_token(request: RefreshTokenRequest):
    """Refresh access token using refresh token."""
    from app.core.security import refresh_token_with_keycloak

    try:
        token_response = await refresh_token_with_keycloak(request.refresh_token)
        return Token(
            access_token=token_response.get("access_token"),
            token_type="Bearer",
            expires_in=token_response.get("expires_in", 3600),
            refresh_token=token_response.get("refresh_token"),
            refresh_expires_in=token_response.get("refresh_expires_in", 86400),
        )
    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        raise HTTPException(status_code=401, detail="Failed to refresh token")


@router.get("/user-info", response_model=UserInfo)
async def get_user_info(current_user: User = Depends(get_current_active_user)):
    """Return info about the current authenticated user."""
    logger.debug(f"User data for user info: {current_user}")

    # Extract roles from realm_access
    roles = current_user.realm_access.get("roles", ["dashboard-user"])

    # Create user info
    user_info = UserInfo(
        sub=current_user.sub,
        preferred_username=current_user.preferred_username,
        email=current_user.email or "default@example.com",
        name=current_user.name or current_user.preferred_username,
        given_name=current_user.name.split()[0] if current_user.name else "User",
        family_name=(
            current_user.name.split()[-1]
            if current_user.name and len(current_user.name.split()) > 1
            else ""
        ),
        roles=roles,
    )

    logger.debug(f"Returning user info: {user_info}")
    return user_info


@router.get("/debug-headers")
async def debug_headers(request: Request):
    """Debug endpoint to show all headers."""
    return {"headers": dict(request.headers), "cookies": request.cookies}


@router.get("/userinfo-token", response_model=UserInfo)
async def get_userinfo_token(current_user: dict = Depends(get_current_user_dual_auth)):
    """Get current user information - supports both Keycloak and API token auth."""

    # Handle both User object (Keycloak) and dict (API token) formats
    if hasattr(current_user, 'sub'):
        # Keycloak User object
        return UserInfo(
            sub=current_user.sub,
            preferred_username=current_user.preferred_username,
            email=current_user.email,
            name=current_user.name,
            roles=current_user.realm_access.get("roles", []),
        )
    else:
        # API token dict
        return UserInfo(
            sub=current_user.get("sub", current_user.get("username", "api-user")),
            preferred_username=current_user.get("preferred_username", current_user.get("username", "api-user")),
            email=current_user.get("email", "api@example.com"),
            name=current_user.get("name", current_user.get("username", "API User")),
            roles=current_user.get("roles", ["api-user"]),
        )
