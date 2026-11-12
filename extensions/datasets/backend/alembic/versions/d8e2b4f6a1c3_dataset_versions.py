"""dataset versions

Revision ID: d8e2b4f6a1c3
Revises: c3a1d7e5f902
Create Date: 2026-10-27 00:00:00

Each dataset's files, columns and mapping become its version 1. A ready
version 1 is `identifying` until app/identify.py, which start.sh runs after
the migrations, has rewritten its files with `_row` and `_origin`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e2b4f6a1c3"
down_revision: Union[str, None] = "c3a1d7e5f902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MOVED = ("status", "error", "splits", "columns", "mapping", "mlflow_experiment_id", "mlflow_run_id")


def upgrade() -> None:
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("splits", sa.JSON(), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=True),
        sa.Column("mlflow_experiment_id", sa.String(length=32), nullable=True),
        sa.Column("mlflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("made_from", sa.Integer(), nullable=True),
        sa.Column("made_by", sa.String(length=16), nullable=False),
        sa.Column("recipe", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "number", name="uq_dataset_versions_number"),
        sa.CheckConstraint("made_by IN ('import', 'upload', 'recipe')", name="ck_dataset_versions_made_by"),
        sa.CheckConstraint("(made_by = 'recipe') = (made_from IS NOT NULL AND recipe IS NOT NULL)",
                           name="ck_dataset_versions_lineage"),
    )
    op.create_index(op.f("ix_dataset_versions_dataset_id"), "dataset_versions", ["dataset_id"], unique=False)
    op.add_column("datasets", sa.Column("next_row", sa.BigInteger(), server_default="0", nullable=False))
    op.execute("""
        INSERT INTO dataset_versions (id, dataset_id, number, status, error, splits, columns, mapping,
                                      mlflow_experiment_id, mlflow_run_id, made_from, made_by, recipe,
                                      created_by, created_at)
        SELECT gen_random_uuid()::text, id, 1,
               CASE WHEN status = 'ready' THEN 'identifying' ELSE status END,
               error, splits, columns, mapping, mlflow_experiment_id, mlflow_run_id, NULL,
               CASE WHEN source = 'huggingface' THEN 'import' ELSE 'upload' END, NULL,
               created_by, created_at
        FROM datasets
    """)
    for column in MOVED:
        op.drop_column("datasets", column)


def downgrade() -> None:
    op.add_column("datasets", sa.Column("status", sa.String(length=16), nullable=True))
    op.add_column("datasets", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("datasets", sa.Column("splits", sa.JSON(), nullable=True))
    op.add_column("datasets", sa.Column("columns", sa.JSON(), nullable=True))
    op.add_column("datasets", sa.Column("mapping", sa.JSON(), nullable=True))
    op.add_column("datasets", sa.Column("mlflow_experiment_id", sa.String(length=32), nullable=True))
    op.add_column("datasets", sa.Column("mlflow_run_id", sa.String(length=64), nullable=True))
    op.execute("""
        UPDATE datasets d SET status = CASE WHEN v.status = 'identifying' THEN 'ready' ELSE v.status END,
               error = v.error, splits = v.splits, columns = v.columns, mapping = v.mapping,
               mlflow_experiment_id = v.mlflow_experiment_id, mlflow_run_id = v.mlflow_run_id
        FROM dataset_versions v WHERE v.dataset_id = d.id AND v.number = 1
    """)
    for column in ("status", "splits", "columns"):
        op.alter_column("datasets", column, nullable=False)
    op.drop_column("datasets", "next_row")
    op.drop_index(op.f("ix_dataset_versions_dataset_id"), table_name="dataset_versions")
    op.drop_table("dataset_versions")
