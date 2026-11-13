# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Copy the images, mirror jobs and custom images that Thinkube Control kept
in its database into this extension's database.

For a cluster that ran Images inside Thinkube Control. Run it once, in the
backend's pod, after the extension's first deploy:

    python -m app.jobs.copy_from_control

Control's database is thinkube_control on the same PostgreSQL server, read
with this backend's login. Running it again copies nothing twice: a row
whose id is already here is left as it is.

- An image already here under the same registry, repository and tag (the
  platform's images, read from the cluster at start) keeps its row and
  receives what a person set in Control: base mark, template, description.
- A job or a build that Control had not finished is copied as failed: the
  process that ran it was Control's.
"""

import sys
from typing import Any, Dict

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.session import get_session_local
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.models.custom_images import CustomImageBuild

CONTROL_DATABASE = "thinkube_control"
INTERRUPTED = "Thinkube Control ran this and did not finish it before Images became an extension"


def _fields(model, row: Dict[str, Any]) -> Dict[str, Any]:
    """The row's values for the columns the model has."""
    return {c.name: row[c.name] for c in model.__table__.columns if c.name in row}


def copy() -> Dict[str, int]:
    """Copy the three tables; how many rows of each were added or updated."""
    source = create_engine(make_url(settings.DATABASE_URL).set(database=CONTROL_DATABASE))
    counts = {"images added": 0, "images updated": 0, "jobs added": 0, "custom images added": 0}
    db = get_session_local()()
    try:
        with source.connect() as control:
            for row in control.execute(text("SELECT * FROM container_images")).mappings():
                if db.get(ContainerImage, row["id"]):
                    continue
                here = db.query(ContainerImage).filter_by(
                    registry=row["registry"], repository=row["repository"], tag=row["tag"]).first()
                if here:
                    here.is_base, here.template = row["is_base"], row["template"]
                    here.description = row["description"] or here.description
                    here.digest = row["digest"] or here.digest
                    counts["images updated"] += 1
                else:
                    db.add(ContainerImage(**_fields(ContainerImage, row)))
                    counts["images added"] += 1
            for row in control.execute(text("SELECT * FROM image_mirror_jobs")).mappings():
                if db.get(ImageMirrorJob, row["id"]):
                    continue
                job = ImageMirrorJob(**_fields(ImageMirrorJob, row))
                if job.status in ("pending", "running"):
                    job.status, job.error_message = "failed", INTERRUPTED
                db.add(job)
                counts["jobs added"] += 1
            # A parent before the images that extend it
            builds = list(control.execute(
                text("SELECT * FROM custom_image_builds ORDER BY created_at")).mappings())
            for row in builds:
                if db.get(CustomImageBuild, row["id"]):
                    continue
                build = CustomImageBuild(**_fields(CustomImageBuild, row))
                # Control kept the path of a log file in its own pod here; this backend keeps the log itself
                build.output = None
                if build.status == "building":
                    build.status, build.output = "failed", INTERRUPTED
                db.add(build)
                db.flush()
                counts["custom images added"] += 1
        db.commit()
    finally:
        db.close()
        source.dispose()
    return counts


if __name__ == "__main__":
    for what, count in copy().items():
        print(f"{what}: {count}")
    sys.exit(0)
