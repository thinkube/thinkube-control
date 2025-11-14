"""
Database models for secrets management
Stores encrypted API keys and secrets for applications
"""

from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime,
    Integer,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class Secret(Base):
    """Store encrypted secrets/API keys"""

    __tablename__ = "secrets"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, unique=True)  # e.g., HF_TOKEN
    description = Column(Text, nullable=True)
    encrypted_value = Column(Text, nullable=False)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # User tracking
    created_by = Column(String(255), nullable=False)
    updated_by = Column(String(255), nullable=True)

    # Relationships
    app_secrets = relationship(
        "AppSecret", back_populates="secret", cascade="all, delete-orphan"
    )

    def to_dict(self, include_value=False):
        """Convert to dictionary for API responses"""
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "used_by_apps": [app_secret.app_name for app_secret in self.app_secrets],
        }
        if include_value:
            result["encrypted_value"] = self.encrypted_value
        return result


class AppSecret(Base):
    """Track which apps use which secrets"""

    __tablename__ = "app_secrets"

    id = Column(Integer, primary_key=True)
    app_name = Column(String(255), nullable=False)
    secret_id = Column(
        Integer, ForeignKey("secrets.id", ondelete="CASCADE"), nullable=False
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    secret = relationship("Secret", back_populates="app_secrets")

    # Ensure unique app_name + secret_id combination
    __table_args__ = (UniqueConstraint("app_name", "secret_id", name="_app_secret_uc"),)

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "app_name": self.app_name,
            "secret_id": self.secret_id,
            "secret_name": self.secret.name if self.secret else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
