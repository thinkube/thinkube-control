"""a dataset keeps what its source says of itself, such as a treebank's licence

Revision ID: f1b3d5a7c9e2
Revises: e4a7c9d1f3b5
Create Date: 2026-11-26 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1b3d5a7c9e2"
down_revision: Union[str, None] = "e4a7c9d1f3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "description")
