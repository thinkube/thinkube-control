"""runs keep the GGUF export of their model

Revision ID: a7d2e9c4b6f1
Revises: f3c9a1b7d5e2
Create Date: 2026-10-23 01:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d2e9c4b6f1"
down_revision: Union[str, None] = "f3c9a1b7d5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("export", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "export")
