"""A notebook run on its own server, started and stopped by thinkube-control."""

import uuid

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.session import Base


class NotebookJob(Base):
    """One unattended run of a notebook.

    The run happens on a named JupyterHub server (``server_name``) started with
    the resources recorded here; the notebook's own outputs land in its file.
    ``status`` moves starting -> running -> completed | error | cancelled | lost.
    """

    __tablename__ = "notebook_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_name = Column(String(63), nullable=False)
    notebook_path = Column(Text, nullable=False)
    kernel_name = Column(String(255), nullable=True)

    node = Column(String(255), nullable=True)
    cpu_cores = Column(Integer, nullable=True)
    memory_gb = Column(Integer, nullable=True)
    gpus = Column(Integer, nullable=False, default=0)

    status = Column(String(32), nullable=False, default="starting")
    execution_id = Column(String(64), nullable=True)
    total_cells = Column(Integer, nullable=True)
    completed_cells = Column(Integer, nullable=True)
    failed_cell_index = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    results = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "job_id": str(self.id),
            "server_name": self.server_name,
            "notebook_path": self.notebook_path,
            "kernel_name": self.kernel_name,
            "node": self.node,
            "cpu_cores": self.cpu_cores,
            "memory_gb": self.memory_gb,
            "gpus": self.gpus,
            "status": self.status,
            "execution_id": self.execution_id,
            "total_cells": self.total_cells,
            "completed_cells": self.completed_cells,
            "failed_cell_index": self.failed_cell_index,
            "error": self.error,
            "results": self.results,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
