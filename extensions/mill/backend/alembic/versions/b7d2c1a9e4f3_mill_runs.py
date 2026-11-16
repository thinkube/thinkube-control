# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""mill_runs

Revision ID: b7d2c1a9e4f3
Revises: a1c4e7f2b9d0
Create Date: 2026-11-16 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2c1a9e4f3'
down_revision: Union[str, None] = 'a1c4e7f2b9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('mill_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('info', sa.JSON(), nullable=False),
    sa.Column('status', sa.JSON(), nullable=True),
    sa.Column('samples', sa.JSON(), nullable=True),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('status_seq', sa.Integer(), nullable=False),
    sa.Column('samples_seq', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mill_runs_started_at'), 'mill_runs', ['started_at'], unique=False)
    op.create_table('mill_failures',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('at', sa.String(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('fields', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('run_id', 'n')
    )
    op.create_table('mill_logs',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('run_id', 'seq')
    )


def downgrade() -> None:
    op.drop_table('mill_logs')
    op.drop_table('mill_failures')
    op.drop_index(op.f('ix_mill_runs_started_at'), table_name='mill_runs')
    op.drop_table('mill_runs')
