"""run_reuse: the earlier plan a run's plan reuses the measurements of, and a request to measure again

Revision ID: b8d0f2a4c6e7
Revises: a7c9e1b3d5f6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d0f2a4c6e7"
down_revision: Union[str, None] = "a7c9e1b3d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("reuse", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("measure", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("runs", "measure")
    op.drop_column("runs", "reuse")
