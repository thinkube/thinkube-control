# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""mill_queued_runs: runs waiting for room in the main pod

Revision ID: f2a6c5e3d8b7
Revises: d9f4e3c1b6a5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a6c5e3d8b7'
down_revision: Union[str, None] = 'd9f4e3c1b6a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mill_queued_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('request', sa.JSON(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_mill_queued_runs_queued_at'), 'mill_queued_runs', ['queued_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mill_queued_runs_queued_at'), table_name='mill_queued_runs')
    op.drop_table('mill_queued_runs')
