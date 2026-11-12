"""runs milestones: Meep²'s milestones of a run, and the evaluation set they score

Revision ID: e4a6c8f0b2d5
Revises: d2f4a6c8e0b3
Create Date: 2026-10-30 17:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e4a6c8f0b2d5"
down_revision: Union[str, None] = "d2f4a6c8e0b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("milestones", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "milestones")
