# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Copy the mirror jobs that Thinkube Control kept in its database into this
extension's database.

For a cluster that ran Models inside Thinkube Control. Run it once, in the
backend's pod, after the extension's first deploy:

    python -m app.jobs.copy_from_control

Control's database is thinkube_control on the same PostgreSQL server, read
with this backend's login. Running it again copies nothing twice: a model
that already has a job here keeps it.

A job that was still running keeps its status: its Argo workflow is the
same, and this extension follows it to its end.
"""

import sys
from typing import Dict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.session import get_session_local
from app.models.model_mirrors import ModelMirrorJob

CONTROL_DATABASE = "thinkube_control"


def copy() -> Dict[str, int]:
    """Copy the table model_mirror_jobs; how many rows were added."""
    source = create_engine(make_url(settings.DATABASE_URL).set(database=CONTROL_DATABASE))
    counts = {"jobs added": 0}
    columns = [c.name for c in ModelMirrorJob.__table__.columns]
    db = get_session_local()()
    try:
        with source.connect() as control:
            for row in control.execute(text("SELECT * FROM model_mirror_jobs")).mappings():
                if db.query(ModelMirrorJob).filter_by(model_id=row["model_id"]).first():
                    continue
                db.add(ModelMirrorJob(**{c: row[c] for c in columns}))
                counts["jobs added"] += 1
        db.commit()
    finally:
        db.close()
        source.dispose()
    return counts


if __name__ == "__main__":
    for what, count in copy().items():
        print(f"{what}: {count}")
    sys.exit(0)
