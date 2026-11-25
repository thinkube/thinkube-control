# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""holds: GPUs kept for work whose pod its owner starts

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every row made before is a job
    op.add_column('jobs', sa.Column('kind', sa.String(), nullable=False, server_default='job'))
    op.alter_column('jobs', 'kind', server_default=None)
    op.add_column('jobs', sa.Column('on_gpus', sa.JSON(), nullable=True))
    op.add_column('jobs', sa.Column('release_url', sa.String(), nullable=True))
    op.alter_column('jobs', 'image', existing_type=sa.String(), nullable=True)
    op.alter_column('jobs', 'command', existing_type=sa.JSON(), nullable=True)


def downgrade() -> None:
    op.alter_column('jobs', 'command', existing_type=sa.JSON(), nullable=False)
    op.alter_column('jobs', 'image', existing_type=sa.String(), nullable=False)
    op.drop_column('jobs', 'release_url')
    op.drop_column('jobs', 'on_gpus')
    op.drop_column('jobs', 'kind')
