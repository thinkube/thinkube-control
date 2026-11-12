"""ended runs leave the queue: no position on a completed, failed or cancelled run

Revision ID: d2f4a6c8e0b3
Revises: c8e2a4f6b0d1
Create Date: 2026-10-28 13:00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = "d2f4a6c8e0b3"
down_revision: Union[str, None] = "c8e2a4f6b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE runs SET position = NULL WHERE status IN ('completed', 'failed', 'cancelled')")


def downgrade() -> None:
    pass
