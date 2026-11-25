# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""mill_jobs: task runs on a chosen node, given to the Scheduler

Revision ID: d9f4e3c1b6a5
Revises: c8e3d2b0f5a4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9f4e3c1b6a5'
down_revision: Union[str, None] = 'c8e3d2b0f5a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mill_jobs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('scheduler_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('node', sa.String(), nullable=False),
        sa.Column('request', sa.JSON(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_mill_jobs_created_at'), 'mill_jobs', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mill_jobs_created_at'), table_name='mill_jobs')
    op.drop_table('mill_jobs')
