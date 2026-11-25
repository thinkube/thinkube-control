# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""priorities: the priority of each kind of work on the nodes

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'priorities',
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('kind'),
    )


def downgrade() -> None:
    op.drop_table('priorities')
