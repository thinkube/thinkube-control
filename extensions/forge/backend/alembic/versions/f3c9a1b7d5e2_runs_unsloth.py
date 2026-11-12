"""runs may train with Unsloth

Revision ID: f3c9a1b7d5e2
Revises: e8b4f2a6c1d3
Create Date: 2026-10-23 00:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3c9a1b7d5e2"
down_revision: Union[str, None] = "e8b4f2a6c1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("unsloth", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("runs", "unsloth")
