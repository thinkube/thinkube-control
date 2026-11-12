"""runs.model_id without an index, as the model declares it

The backend's start script generated this revision when the model and the
migrations differed, and applied it to the deployed database; it is kept here
so the history matches that database.

Revision ID: 16d6a682ca11
Revises: c41d9e7a2b10
"""
from typing import Sequence, Union

from alembic import op

revision: str = "16d6a682ca11"
down_revision: Union[str, None] = "c41d9e7a2b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_runs_model_id", table_name="runs")


def downgrade() -> None:
    op.create_index("ix_runs_model_id", "runs", ["model_id"], unique=False)
