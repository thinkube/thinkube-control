# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
The news and fixes feed: the last feed read from thinkube-fixes, and the news
the user has read.

The status of a fix is not stored: it is computed from the installed component
versions every time it is read.
"""

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.db.session import Base


class FixFeed(Base):
    """The one row holding the last feed check and the last valid feed."""

    __tablename__ = "fix_feed"

    id = Column(Integer, primary_key=True)
    # The repository the last check fetched.
    url = Column(Text, nullable=True)
    last_checked = Column(DateTime(timezone=True), nullable=True)
    # The failure of the last check, empty when it succeeded.
    last_error = Column(Text, nullable=True)
    # The last feed that passed validation; a failed check leaves it unchanged.
    payload = Column(JSON, nullable=True)
    payload_fetched = Column(DateTime(timezone=True), nullable=True)
    # The commit of thinkube-fixes the payload was read from.
    feed_commit = Column(String(40), nullable=True)


class NewsRead(Base):
    """A news entry the user has marked read."""

    __tablename__ = "news_read"

    news_id = Column(String(255), primary_key=True)
    read_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
