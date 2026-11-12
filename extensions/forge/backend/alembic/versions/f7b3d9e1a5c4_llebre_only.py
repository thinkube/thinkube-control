"""runs train with llebre only: no Unsloth runs, recipe runs or TRL settings

Revision ID: f7b3d9e1a5c4
Revises: e5a1c7b9d3f2
Create Date: 2026-10-27 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7b3d9e1a5c4"
down_revision: Union[str, None] = "e5a1c7b9d3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("recipe_runs")
    op.drop_column("runs", "unsloth")
    op.drop_column("runs", "engine")
    op.drop_column("runs", "hyper")


def downgrade() -> None:
    op.add_column("runs", sa.Column("hyper", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("engine", sa.String(length=16), server_default="llebre", nullable=False))
    op.add_column("runs", sa.Column("unsloth", sa.Boolean(), server_default=sa.false(), nullable=False))
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
