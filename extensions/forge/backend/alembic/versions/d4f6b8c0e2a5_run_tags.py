"""run_tags: names and values that link a run with what it is for

Revision ID: d4f6b8c0e2a5
Revises: c3e5a7b9d1f4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6b8c0e2a5"
down_revision: Union[str, None] = "c3e5a7b9d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "tags")
