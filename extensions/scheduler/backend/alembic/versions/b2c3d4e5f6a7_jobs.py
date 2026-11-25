# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""jobs: the commands the scheduler runs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('owner', sa.String(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('node', sa.String(), nullable=False),
    sa.Column('image', sa.String(), nullable=False),
    sa.Column('command', sa.JSON(), nullable=False),
    sa.Column('env', sa.JSON(), nullable=False),
    sa.Column('working_dir', sa.String(), nullable=True),
    sa.Column('mounts', sa.JSON(), nullable=False),
    sa.Column('gpus', sa.Integer(), nullable=False),
    sa.Column('gpu_memory_gb', sa.Float(), nullable=True),
    sa.Column('memory_gb', sa.Integer(), nullable=False),
    sa.Column('cpu_cores', sa.Integer(), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('resumable', sa.Boolean(), nullable=False),
    sa.Column('stop_seconds', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('gpu_ids', sa.JSON(), nullable=True),
    sa.Column('pod_job', sa.String(), nullable=True),
    sa.Column('starts', sa.Integer(), nullable=False),
    sa.Column('waiting', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_jobs_created_at'), 'jobs', ['created_at'], unique=False)
    op.create_index(op.f('ix_jobs_node'), 'jobs', ['node'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_node'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_created_at'), table_name='jobs')
    op.drop_table('jobs')
