"""the training queue: runs as llebre jobs in a queue per node, slots, and the worker's record

Revision ID: d2f6a8c0e4b7
Revises: b5e8c3d1a9f4
Create Date: 2026-10-27 03:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2f6a8c0e4b7"
down_revision: Union[str, None] = "b5e8c3d1a9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("engine", sa.String(length=16), server_default="llebre", nullable=False))
    # Every run before this revision trained with the TRL notebook
    op.execute("UPDATE runs SET engine = 'trl'")
    op.alter_column("runs", "hyper", existing_type=sa.JSON(), nullable=True)
    op.add_column("runs", sa.Column("job", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("plan", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("plan_job_id", sa.String(length=36), nullable=True))
    op.add_column("runs", sa.Column("progress", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("run_when", sa.String(length=8), server_default="now", nullable=False))
    op.add_column("runs", sa.Column("across_slots", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("runs", sa.Column("position", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("queue_on_plan", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("runs", sa.Column("paused_by", sa.String(length=8), nullable=True))
    op.add_column("runs", sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("memory_gb", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("cpu_cores", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("mlflow_experiment_id", sa.String(length=64), nullable=True))
    op.add_column("runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    # A run's status is its place in the queue; Control's job states map onto it
    op.execute("UPDATE runs SET status = 'failed' WHERE status IN ('error', 'lost')")
    op.execute("UPDATE runs SET status = 'failed', error = 'its notebook job is no longer followed by Forge' "
               "WHERE status IN ('starting', 'running')")

    op.create_table(
        "slots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("node", sa.String(length=255), nullable=False),
        sa.Column("days", sa.JSON(), nullable=False),
        sa.Column("start", sa.String(length=5), nullable=False),
        sa.Column("end", sa.String(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "queue_nodes",
        sa.Column("node", sa.String(length=255), nullable=False),
        sa.Column("training_mode", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("slot_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("node"),
    )
    op.create_table(
        "worker_credentials",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("worker_credentials")
    op.drop_table("queue_nodes")
    op.drop_table("slots")
    # The revision before this one has no llebre runs
    op.execute("DELETE FROM runs WHERE engine = 'llebre'")
    for column in ("started_at", "mlflow_experiment_id", "cpu_cores", "memory_gb", "stop_requested_at", "paused_by",
                   "queue_on_plan", "position", "across_slots", "run_when", "progress", "plan_job_id", "plan", "job",
                   "engine"):
        op.drop_column("runs", column)
    op.execute("UPDATE runs SET status = 'error' WHERE status = 'failed'")
    op.alter_column("runs", "hyper", existing_type=sa.JSON(), nullable=False)
