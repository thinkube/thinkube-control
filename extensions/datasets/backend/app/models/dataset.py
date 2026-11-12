# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A dataset and its numbered versions.

A dataset is a name and where it came from. Its rows live in its versions:
each version is one MLflow run whose artifacts are its splits, one Parquet
file per split, and records the version it was made from and what made it.
A version's files, splits and lineage do not change once it is ready; its
mapping of columns to examples can be set.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import relationship

from app.db.session import Base

# What makes a version: an import from the Hub, an upload, or a recipe
MADE_BY = ("import", "upload", "recipe")
# importing, ready or failed; identifying while the files of a version that
# lack `_row` and `_origin` are rewritten with them (app/identify.py)
STATUSES = ("importing", "identifying", "ready", "failed")


def _now():
    return datetime.now(timezone.utc)


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    # upload or huggingface, and the file name or repo[:config] it came from
    source = Column(String(16), nullable=False)
    origin = Column(String(512), nullable=False)
    # The `_row` the next new row of any version gets
    next_row = Column(BigInteger, nullable=False, server_default="0")
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    versions = relationship("DatasetVersion", back_populates="dataset", cascade="all, delete-orphan",
                            order_by="DatasetVersion.number", passive_deletes=True)

    def described(self):
        """The version the dataset's routes act on: the latest ready one, or
        the latest of all while none is ready."""
        ready = [v for v in self.versions if v.status == "ready"]
        return (ready or self.versions)[-1]

    def to_dict(self):
        version = self.described()
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "origin": self.origin,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "versions": len(self.versions),
            "latest": self.versions[-1].number,
            **version.fields(),
        }


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "number", name="uq_dataset_versions_number"),
        CheckConstraint("made_by IN ('import', 'upload', 'recipe')", name="ck_dataset_versions_made_by"),
        # A recipe reads a version and is kept with what it made; an import or upload reads none
        CheckConstraint("(made_by = 'recipe') = (made_from IS NOT NULL AND recipe IS NOT NULL)",
                        name="ck_dataset_versions_lineage"),
        # A version in the making is made by an Argo workflow, which the backend follows
        CheckConstraint("status <> 'importing' OR workflow IS NOT NULL", name="ck_dataset_versions_workflow"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id = Column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    # 1, 2, ... within the dataset
    number = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)
    error = Column(Text, nullable=True)
    # [{"name", "rows", "bytes"}], in the order the dataset gave them
    splits = Column(JSON, nullable=False)
    # The data columns, without `_row` and `_origin`
    columns = Column(JSON, nullable=False)
    mapping = Column(JSON, nullable=True)
    mlflow_experiment_id = Column(String(32), nullable=True)
    mlflow_run_id = Column(String(64), nullable=True)
    # The Argo workflow that makes or made the version, in WORKFLOWS_NAMESPACE
    workflow = Column(String(255), nullable=True)
    # Lineage: the number of the version this one was made from, and what made it.
    # recipe is {"id": ..., "name": ..., "document": {the recipe's steps and settings}}
    made_from = Column(Integer, nullable=True)
    made_by = Column(String(16), nullable=False)
    recipe = Column(JSON, nullable=True)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    dataset = relationship("Dataset", back_populates="versions")

    def fields(self):
        return {
            "version": self.number,
            "status": self.status,
            "error": self.error,
            "splits": self.splits,
            "columns": self.columns,
            "mapping": self.mapping,
            "mlflow_run_id": self.mlflow_run_id,
            "workflow": self.workflow,
        }

    def to_dict(self):
        return {
            "dataset_id": self.dataset_id,
            **self.fields(),
            "rows": sum(s["rows"] for s in self.splits),
            "made_from": self.made_from,
            "made_by": self.made_by,
            "recipe": self.recipe,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }
