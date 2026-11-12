"""priorities: of each run, and of each kind of work on a GPU node

Revision ID: e5a1c7b9d3f2
Revises: d2f6a8c0e4b7
Create Date: 2026-10-27 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a1c7b9d3f2"
down_revision: Union[str, None] = "d2f6a8c0e4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The defaults the person decided: 1 is the highest priority
DEFAULTS = {"notebook_server": 1, "notebook_job": 2, "forge_run": 3, "served_model": 4, "app": 5}


def upgrade() -> None:
    op.add_column("runs", sa.Column("priority", sa.Integer(), server_default="3", nullable=False))
    op.add_column("runs", sa.Column("preempted_by", sa.String(length=255), nullable=True))
    op.add_column("runs", sa.Column("waiting", sa.Text(), nullable=True))
    op.alter_column("runs", "paused_by", existing_type=sa.String(length=8), type_=sa.String(length=16))
    op.add_column("queue_nodes", sa.Column("held_for", sa.String(length=8), nullable=True))
    op.execute("UPDATE queue_nodes SET held_for = 'slot' WHERE training_mode")
    table = op.create_table(
        "priorities",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("kind"),
    )
    op.bulk_insert(table, [{"kind": k, "priority": v} for k, v in DEFAULTS.items()])


def downgrade() -> None:
    op.drop_table("priorities")
    op.drop_column("queue_nodes", "held_for")
    op.execute("UPDATE runs SET paused_by = 'user' WHERE paused_by = 'preempted'")
    op.alter_column("runs", "paused_by", existing_type=sa.String(length=16), type_=sa.String(length=8))
    for column in ("waiting", "preempted_by", "priority"):
        op.drop_column("runs", column)
