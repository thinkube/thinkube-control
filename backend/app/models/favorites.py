"""User favorites model for tracking starred services"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class UserFavorite(Base):
    """Model for tracking user's favorite services"""

    __tablename__ = "user_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "service_id", name="unique_user_service_favorite"),
    )

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # User ID from Keycloak
    user_id = Column(String(255), nullable=False, index=True)

    # Foreign key to service
    service_id = Column(
        UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Order for drag and drop
    order_index = Column(Integer, nullable=True, default=0)

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship
    service = relationship("Service", backref="favorites")

    def __repr__(self):
        return f"<UserFavorite user={self.user_id} service={self.service_id}>"
