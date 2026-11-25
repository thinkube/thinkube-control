"""the Scheduler owns the turn: Forge keeps no priorities of other work and no record of training mode

Revision ID: c1d3e5f7a9b2
Revises: b8d0f2a4c6e7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d3e5f7a9b2"
down_revision: Union[str, None] = "b8d0f2a4c6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULTS = {"notebook_server": 1, "notebook_job": 2, "forge_run": 3, "served_model": 4, "app": 5}


def upgrade() -> None:
    op.drop_table("priorities")
    op.drop_table("queue_nodes")


def downgrade() -> None:
    op.create_table(
        "queue_nodes",
        sa.Column("node", sa.String(length=255), nullable=False),
        sa.Column("training_mode", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("held_for", sa.String(length=8), nullable=True),
        sa.Column("slot_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("node"),
    )
    table = op.create_table(
        "priorities",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("kind"),
    )
    op.bulk_insert(table, [{"kind": k, "priority": v} for k, v in DEFAULTS.items()])
