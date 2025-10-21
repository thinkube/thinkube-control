# app/core/security.py
from typing import Dict, Optional, List
import logging
from datetime import datetime, timedelta
import httpx
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import OAuth2AuthorizationCodeBearer
from pydantic import BaseModel
from app.core.config import settings

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Define OAuth2 scheme for Keycloak
oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/auth",
    tokenUrl=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token",
    refreshUrl=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token",
    auto_error=False,  # Don't auto-error to allow API token authentication
    scopes={
        "openid": "OpenID Connect scope",
        "profile": "Access user profile",
        "email": "Access user email",
    },
)


class TokenData(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    roles: List[str] = []


class User(BaseModel):
    sub: str
    preferred_username: str
    email: Optional[str] = None
    name: Optional[str] = None
    realm_access: Dict[str, List[str]] = {"roles": []}


# Cache for Keycloak public key
_keycloak_public_key = None
_key_cache_time = None
KEY_CACHE_DURATION = timedelta(hours=1)


async def get_keycloak_public_key() -> str:
    """Fetch and cache Keycloak public key for token verification."""
    global _keycloak_public_key, _key_cache_time

    # Return cached key if still valid
    if _keycloak_public_key and _key_cache_time:
        if datetime.utcnow() - _key_cache_time < KEY_CACHE_DURATION:
            return _keycloak_public_key

    # Fetch new key
    try:
        async with httpx.AsyncClient(verify=settings.KEYCLOAK_VERIFY_SSL) as client:
            response = await client.get(
                f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}"
            )
            response.raise_for_status()
            realm_info = response.json()

            # Format the public key
            public_key = realm_info.get("public_key", "")
            formatted_key = (
                f"-----BEGIN PUBLIC KEY-----\n{public_key}\n-----END PUBLIC KEY-----"
            )

            # Cache the key
            _keycloak_public_key = formatted_key
            _key_cache_time = datetime.utcnow()

            return formatted_key

    except Exception as e:
        logger.error(f"Failed to fetch Keycloak public key: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to fetch authentication configuration",
        )


async def verify_token(token: str) -> Dict:
    """Verify and decode a Keycloak JWT token."""
    try:
        # Get the public key
        public_key = await get_keycloak_public_key()

        # Decode and verify the token
        # For public clients, the audience might be "account" or the client_id
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Skip audience verification for now
            issuer=f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        )

        # Log the actual audience for debugging
        logger.debug(f"Token audience: {payload.get('aud', 'No audience')}")

        return payload

    except JWTError as e:
        logger.error(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """Extract and validate user from JWT token."""

    # Verify the token
    payload = await verify_token(token)

    # Extract user information
    user = User(
        sub=payload.get("sub", ""),
        preferred_username=payload.get("preferred_username", ""),
        email=payload.get("email"),
        name=payload.get("name", payload.get("preferred_username", "")),
        realm_access=payload.get("realm_access", {"roles": []}),
    )

    logger.debug(f"Authenticated user: {user.preferred_username}")
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Return the current active user."""
    return current_user


def require_role(required_role: str):
    """Dependency to check if user has a specific role."""

    async def role_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        user_roles = current_user.realm_access.get("roles", [])
        if required_role not in user_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User does not have required role: {required_role}",
            )
        return current_user

    return role_checker


# Optional: Function to exchange authorization code for token (for frontend integration)
async def exchange_code_for_token(code: str, redirect_uri: str) -> Dict:
    """Exchange authorization code for access token."""
    try:
        async with httpx.AsyncClient(verify=settings.KEYCLOAK_VERIFY_SSL) as client:
            # For public clients, don't send client_secret
            token_data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.KEYCLOAK_CLIENT_ID,
            }

            response = await client.post(
                f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token",
                data=token_data,
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Token exchange failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to exchange authorization code",
        )


# Function to refresh token with Keycloak
async def refresh_token_with_keycloak(refresh_token: str) -> Dict:
    """Refresh access token using refresh token."""
    try:
        async with httpx.AsyncClient(verify=settings.KEYCLOAK_VERIFY_SSL) as client:
            # For public clients, don't send client_secret
            token_data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.KEYCLOAK_CLIENT_ID,
            }

            response = await client.post(
                f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token",
                data=token_data,
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Failed to refresh token"
        )
