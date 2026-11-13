# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""initial_schema

Revision ID: d1e2f3a4b5c6
Revises:
Create Date: 2026-11-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_used', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('token_metadata', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_table(
        'model_mirror_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('model_id', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('workflow_name', sa.String(length=255), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')", name='check_mirror_status'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_mirror_status', 'model_mirror_jobs', ['status'], unique=False)
    op.create_index('idx_mirror_model_id', 'model_mirror_jobs', ['model_id'], unique=False)
    op.create_index('idx_mirror_created', 'model_mirror_jobs', ['created_at'], unique=False)
    op.create_index(op.f('ix_model_mirror_jobs_model_id'), 'model_mirror_jobs', ['model_id'], unique=True)
    op.create_index(op.f('ix_model_mirror_jobs_status'), 'model_mirror_jobs', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_model_mirror_jobs_status'), table_name='model_mirror_jobs')
    op.drop_index(op.f('ix_model_mirror_jobs_model_id'), table_name='model_mirror_jobs')
    op.drop_index('idx_mirror_created', table_name='model_mirror_jobs')
    op.drop_index('idx_mirror_model_id', table_name='model_mirror_jobs')
    op.drop_index('idx_mirror_status', table_name='model_mirror_jobs')
    op.drop_table('model_mirror_jobs')
    op.drop_table('api_tokens')
