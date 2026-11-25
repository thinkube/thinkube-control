"""tasks are Mill's: a task run Forge kept that has not ended is ended

Mill gives its tasks to the Scheduler itself. A task run of Forge's that is open (queued, paused, running,
stopping) can no longer start: it is cancelled, saying so. Its record and its state folder on the shared file
system stay.

Revision ID: c5e7a9b1d3f4
Revises: c1d3e5f7a9b2
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c5e7a9b1d3f4"
down_revision: Union[str, None] = "c1d3e5f7a9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE runs SET status = 'cancelled', position = NULL, finished_at = now(), stop_requested_at = NULL, "
        "error = 'Mill runs its tasks on a node itself now (mill_run_on_node); this task run of Forge was ended "
        "when Forge stopped running tasks. Its state folder on the shared file system is kept.' "
        "WHERE kind = 'task' AND status IN ('queued', 'paused', 'running', 'stopping')")


def downgrade() -> None:
    # The runs ended here are not opened again: their pods were Forge's, which no longer starts them
    pass
