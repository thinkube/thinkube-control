"""a version records the Argo workflow that makes it

Revision ID: e4a7c9d1f3b5
Revises: d8e2b4f6a1c3
Create Date: 2026-10-28 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4a7c9d1f3b5"
down_revision: Union[str, None] = "d8e2b4f6a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dataset_versions", sa.Column("workflow", sa.String(length=255), nullable=True))
    op.create_check_constraint("ck_dataset_versions_workflow", "dataset_versions",
                               "status <> 'importing' OR workflow IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint("ck_dataset_versions_workflow", "dataset_versions", type_="check")
    op.drop_column("dataset_versions", "workflow")
