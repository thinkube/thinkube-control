"""runs preprocess: the preprocessing of a run's data before its plan

Revision ID: b7c9d1e3f5a2
Revises: e4a6c8f0b2d5
Create Date: 2026-10-31 13:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7c9d1e3f5a2"
down_revision: Union[str, None] = "e4a6c8f0b2d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("preprocess", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "preprocess")
