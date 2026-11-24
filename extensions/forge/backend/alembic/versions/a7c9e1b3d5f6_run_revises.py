"""run_revises: the plan job whose plan a revision of the run's plan starts from

Revision ID: a7c9e1b3d5f6
Revises: d4f6b8c0e2a5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1b3d5f6"
down_revision: Union[str, None] = "d4f6b8c0e2a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("revises", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "revises")
