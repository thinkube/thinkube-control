# app/core/api_tokens.py
"""API Token authentication for programmatic access."""

from typing import Optional, Dict, List
from datetime import datetime, timedelta
import secrets
import hashlib
from sqlalchemy import Column, String, DateTime, Boolean, JSON
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import logging

from app.core.config import settings
from app.db.session import get_db, Base
from app.core.security import oauth2_scheme

logger = logging.getLogger(__name__)


# Database model for API tokens
class APIToken(Base):
    __tablename__ = "api_tokens"

    id = Column(String, primary_key=True, default=lambda: secrets.token_urlsafe(16))
    name = Column(String, nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    user_id = Column(String, nullable=False)  # Keycloak user ID
    username = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    last_used = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    scopes = Column(JSON, default=list)  # List of allowed scopes
    token_metadata = Column(JSON, default=dict)  # Additional metadata


# Pydantic models
class APITokenCreate(BaseModel):
    name: str
    expires_in_days: Optional[int] = None  # None means no expiration
    scopes: List[str] = []


class APITokenResponse(BaseModel):
    id: str
    name: str
    token: str  # Only returned on creation
    created_at: datetime
    expires_at: Optional[datetime]
    scopes: List[str]


class APITokenInfo(BaseModel):
    id: str
    name: str
    created_at: datetime
    expires_at: Optional[datetime]
    last_used: Optional[datetime]
    is_active: bool
    scopes: List[str]


# Security scheme for API tokens
api_token_scheme = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_api_token() -> str:
    """Generate a secure API token."""
    # Format: tk_<random_string>
    return f"tk_{secrets.token_urlsafe(32)}"


async def create_api_token(
    db,
    user_id: str,
    username: str,
    name: str,
    expires_in_days: Optional[int] = None,
    scopes: List[str] = [],
) -> APITokenResponse:
    """Create a new API token for a user."""

    # Generate regular API token
    raw_token = generate_api_token()
    token_hash = hash_token(raw_token)

    # Calculate expiration
    expires_at = None
    if expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

    # Create token record
    db_token = APIToken(
        name=name,
        token_hash=token_hash,
        user_id=user_id,
        username=username,
        expires_at=expires_at,
        scopes=scopes,
    )

    db.add(db_token)
    db.commit()
    db.refresh(db_token)

    # Return response with raw token (only time it's visible)
    return APITokenResponse(
        id=db_token.id,
        name=db_token.name,
        token=raw_token,
        created_at=db_token.created_at,
        expires_at=db_token.expires_at,
        scopes=db_token.scopes,
    )


async def verify_api_token(
    credentials: HTTPAuthorizationCredentials, db
) -> Optional[Dict]:
    """Verify an API token and return user info."""

    if not credentials:
        return None

    token = credentials.credentials
    if not token.startswith("tk_"):
        return None

    token_hash = hash_token(token)

    # Look up token
    db_token = (
        db.query(APIToken)
        .filter(APIToken.token_hash == token_hash, APIToken.is_active == True)
        .first()
    )

    if not db_token:
        return None

    # Check expiration
    if db_token.expires_at and datetime.utcnow() > db_token.expires_at:
        return None

    # Update last used
    db_token.last_used = datetime.utcnow()
    db.commit()

    # Return user info
    return {
        "sub": db_token.user_id,
        "preferred_username": db_token.username,
        "token_id": db_token.id,
        "token_name": db_token.name,
        "scopes": db_token.scopes,
        "auth_method": "api_token",
    }


async def list_api_tokens(db, user_id: str) -> List[APITokenInfo]:
    """List all API tokens for a user."""
    tokens = (
        db.query(APIToken)
        .filter(APIToken.user_id == user_id)
        .order_by(APIToken.created_at.desc())
        .all()
    )

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


async def revoke_api_token(db, user_id: str, token_id: str) -> bool:
    """Revoke an API token."""
    token = (
        db.query(APIToken)
        .filter(APIToken.id == token_id, APIToken.user_id == user_id)
        .first()
    )

    if not token:
        return False

    token.is_active = False
    db.commit()
    return True


# Dependency for dual authentication (Keycloak OR API token)
async def get_current_user_dual_auth(
    keycloak_token: Optional[str] = Depends(oauth2_scheme),
    api_credentials: Optional[HTTPAuthorizationCredentials] = Depends(api_token_scheme),
    db=Depends(get_db),
):
    """Get current user from either Keycloak token or API token."""

    # First try API token
    if api_credentials:
        user_info = await verify_api_token(api_credentials, db)
        if user_info:
            logger.debug(f"Authenticated via API token: {user_info['token_name']}")
            return user_info

    # Then try Keycloak token (but skip if it's an API token format)
    if keycloak_token and not keycloak_token.startswith("tk_"):
        # Import here to avoid circular dependency
        from app.core.security import verify_token, User

        try:
            payload = await verify_token(keycloak_token)
            user_info = {
                "sub": payload.get("sub", ""),
                "preferred_username": payload.get("preferred_username", ""),
                "email": payload.get("email"),
                "name": payload.get("name", payload.get("preferred_username", "")),
                "realm_access": payload.get("realm_access", {"roles": []}),
                "auth_method": "keycloak",
            }
            logger.debug(
                f"Authenticated via Keycloak: {user_info['preferred_username']}"
            )
            return user_info
        except Exception as e:
            logger.debug(f"Keycloak authentication failed: {e}")

    # No valid authentication
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": 'Bearer, ApiKey realm="api"'},
    )


# 🤖 Generated with Claude
