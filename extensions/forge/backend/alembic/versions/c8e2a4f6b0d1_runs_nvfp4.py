"""runs keep their NVFP4 export

Revision ID: c8e2a4f6b0d1
Revises: a3c5e7f9b1d2
Create Date: 2026-10-27 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e2a4f6b0d1"
down_revision: Union[str, None] = "a3c5e7f9b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("nvfp4", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "nvfp4")
