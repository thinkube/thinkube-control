# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""runs_task: a run is a fine-tune or a task of Mill

Revision ID: c3e5a7b9d1f4
Revises: b7c9d1e3f5a2
Create Date: 2026-11-17 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e5a7b9d1f4"
down_revision: Union[str, None] = "b7c9d1e3f5a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# What only a fine-tune has
FINETUNE_ONLY = (
    ("dataset_id", sa.String(36)),
    ("dataset_version", sa.Integer()),
    ("base_model", sa.String(255)),
    ("method", sa.String(16)),
    ("register", sa.Boolean()),
)


def upgrade() -> None:
    op.add_column("runs", sa.Column("kind", sa.String(16), server_default="finetune", nullable=False))
    op.add_column("runs", sa.Column("task", sa.JSON(), nullable=True))
    for name, kind in FINETUNE_ONLY:
        op.alter_column("runs", name, existing_type=kind, nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM runs WHERE kind = 'task'")
    for name, kind in FINETUNE_ONLY:
        op.alter_column("runs", name, existing_type=kind, nullable=False)
    op.drop_column("runs", "task")
    op.drop_column("runs", "kind")
