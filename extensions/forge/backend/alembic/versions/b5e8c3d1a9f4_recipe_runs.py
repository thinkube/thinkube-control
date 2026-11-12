"""recipe runs: notebooks of the Unsloth component that Forge runs

Revision ID: b5e8c3d1a9f4
Revises: a7d2e9c4b6f1
Create Date: 2026-10-23 01:20:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5e8c3d1a9f4"
down_revision: Union[str, None] = "a7d2e9c4b6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recipe_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recipe", sa.String(length=255), nullable=False),
        sa.Column("notebook", sa.String(length=512), nullable=False),
        sa.Column("node", sa.String(length=255), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("recipe_runs")
