# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
SQLAlchemy models for the notebook servers' default resources.

Each node runs at most one interactive notebook server, and each node has its
own defaults: the CPU cores, memory and GPUs a server there starts with.
``jupyterhub_config`` keeps one row with the values a node without its own
row starts from. Maximum limits are calculated from cluster resources.

The image is fixed to tk-jupyter-base; venvs provide the Python environments
through kernel selection.
"""

from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from uuid import uuid4

from app.db.session import Base


class JupyterHubConfig(Base):
    """The values a node without its own defaults starts from (single row)."""

    __tablename__ = "jupyterhub_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    default_cpu_cores = Column(Integer, nullable=False, default=4)
    default_memory_gb = Column(Integer, nullable=False, default=8)
    default_gpu_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return (
            f"<JupyterHubConfig(cpu={self.default_cpu_cores}, "
            f"mem={self.default_memory_gb}GB, gpu={self.default_gpu_count})>"
        )


class JupyterHubNodeDefaults(Base):
    """The resources the notebook server on one node starts with."""

    __tablename__ = "jupyterhub_node_defaults"

    node = Column(String, primary_key=True)
    cpu_cores = Column(Integer, nullable=False)
    memory_gb = Column(Integer, nullable=False)
    gpus = Column(Integer, nullable=False)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<JupyterHubNodeDefaults({self.node}: cpu={self.cpu_cores}, mem={self.memory_gb}GB, gpu={self.gpus})>"
