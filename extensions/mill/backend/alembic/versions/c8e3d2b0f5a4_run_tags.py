# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""run_tags

Revision ID: c8e3d2b0f5a4
Revises: b7d2c1a9e4f3
Create Date: 2026-11-19 01:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e3d2b0f5a4'
down_revision: Union[str, None] = 'b7d2c1a9e4f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('mill_runs', sa.Column('tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('mill_runs', 'tags')
