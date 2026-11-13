# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""initial_schema

Revision ID: c0a1b2d3e4f5
Revises:
Create Date: 2026-11-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c0a1b2d3e4f5'
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
        'container_images',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('registry', sa.String(length=255), nullable=False),
        sa.Column('repository', sa.String(length=500), nullable=False),
        sa.Column('tag', sa.String(length=128), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('destination_url', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('protected', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=False),
        sa.Column('is_base', sa.Boolean(), nullable=False),
        sa.Column('template', sa.Text(), nullable=True),
        sa.Column('mirror_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_synced', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('image_metadata', sa.JSON(), nullable=True),
        sa.Column('harbor_project', sa.String(length=255), nullable=True),
        sa.Column('digest', sa.String(length=255), nullable=True),
        sa.Column('size_bytes', sa.String(length=50), nullable=True),
        sa.Column('vulnerabilities', sa.JSON(), nullable=True),
        sa.Column('usage_count', sa.JSON(), nullable=True),
        sa.Column('last_pulled', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("category IN ('system', 'user')", name='check_image_category'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('registry', 'repository', 'tag', name='unique_image_per_registry'),
    )
    op.create_index('idx_image_category', 'container_images', ['category'], unique=False)
    op.create_index('idx_image_name', 'container_images', ['name'], unique=False)
    op.create_index('idx_image_protected', 'container_images', ['protected'], unique=False)
    op.create_index(op.f('ix_container_images_category'), 'container_images', ['category'], unique=False)
    op.create_index(op.f('ix_container_images_name'), 'container_images', ['name'], unique=False)
    op.create_table(
        'image_mirror_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('image_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('destination_url', sa.Text(), nullable=True),
        sa.Column('image_category', sa.String(length=50), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('logs', sa.Text(), nullable=True),
        sa.Column('workflow', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('job_metadata', sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')", name='check_job_status'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_job_created', 'image_mirror_jobs', ['created_at'], unique=False)
    op.create_index('idx_job_status', 'image_mirror_jobs', ['status'], unique=False)
    op.create_table(
        'custom_image_builds',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('dockerfile_path', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('build_config', sa.JSON(), nullable=True),
        sa.Column('output', sa.Text(), nullable=True),
        sa.Column('workflow', sa.String(length=255), nullable=True),
        sa.Column('registry_url', sa.Text(), nullable=True),
        sa.Column('is_base', sa.Boolean(), nullable=False),
        sa.Column('scope', sa.String(length=50), nullable=False),
        sa.Column('parent_image_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('template', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['parent_image_id'], ['custom_image_builds.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('custom_image_builds')
    op.drop_index('idx_job_status', table_name='image_mirror_jobs')
    op.drop_index('idx_job_created', table_name='image_mirror_jobs')
    op.drop_table('image_mirror_jobs')
    op.drop_index(op.f('ix_container_images_name'), table_name='container_images')
    op.drop_index(op.f('ix_container_images_category'), table_name='container_images')
    op.drop_index('idx_image_protected', table_name='container_images')
    op.drop_index('idx_image_name', table_name='container_images')
    op.drop_index('idx_image_category', table_name='container_images')
    op.drop_table('container_images')
    op.drop_table('api_tokens')
