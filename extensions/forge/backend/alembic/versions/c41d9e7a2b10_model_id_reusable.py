"""A failed run's model id can be given to a later run

Revision ID: c41d9e7a2b10
Revises: b7c2f0d41e2a
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c41d9e7a2b10"
down_revision: Union[str, None] = "b7c2f0d41e2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("runs_model_id_key", "runs", type_="unique")
    op.create_index("ix_runs_model_id", "runs", ["model_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_model_id", table_name="runs")
    op.create_unique_constraint("runs_model_id_key", "runs", ["model_id"])
