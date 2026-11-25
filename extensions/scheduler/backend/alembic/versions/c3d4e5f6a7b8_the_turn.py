# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""the_turn: the job a job was stopped for, and the nodes' limits

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('preempted_by', sa.String(), nullable=True))
    op.create_table('node_limits',
    sa.Column('node', sa.String(), nullable=False),
    sa.Column('gpus_at_work', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('node')
    )


def downgrade() -> None:
    op.drop_table('node_limits')
    op.drop_column('jobs', 'preempted_by')
