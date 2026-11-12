"""runs read their dataset from the Datasets extension

Revision ID: e8b4f2a6c1d3
Revises: 16d6a682ca11
Create Date: 2026-10-23 00:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8b4f2a6c1d3"
down_revision: Union[str, None] = "16d6a682ca11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("runs_dataset_id_fkey", "runs", type_="foreignkey")
    op.add_column("runs", sa.Column("dataset", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("evaluation", sa.JSON(), nullable=True))
    op.drop_table("datasets")


def downgrade() -> None:
    raise NotImplementedError("Forge's own datasets moved to the Datasets extension; there is no way back")
