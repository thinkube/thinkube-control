# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The priority of each kind of work on the nodes, as a person set it."""

from sqlalchemy import Column, Integer, String

from app.db.session import Base


class Priority(Base):
    __tablename__ = "priorities"

    kind = Column(String, primary_key=True)
    # 1 (highest) to 5
    priority = Column(Integer, nullable=False)
