"""runs record the version of the dataset they train on

Revision ID: a3c5e7f9b1d2
Revises: f7b3d9e1a5c4
Create Date: 2026-10-27 00:00:00

A run that exists already read a dataset of the Datasets extension that had
one state, which Datasets keeps as its version 1: those runs get 1.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3c5e7f9b1d2"
down_revision: Union[str, None] = "f7b3d9e1a5c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("dataset_version", sa.Integer(), nullable=True))
    op.execute("UPDATE runs SET dataset_version = 1")
    op.alter_column("runs", "dataset_version", nullable=False)


def downgrade() -> None:
    op.drop_column("runs", "dataset_version")
