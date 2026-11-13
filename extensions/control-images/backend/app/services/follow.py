# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Follows the workflows of the running mirror jobs and image builds, and
records how each one ends.

One pass reads every job with status running and every build with status
building, each with the name of its workflow. Nothing is kept in memory
between passes, so a restart of the backend loses nothing: the next pass
continues from the database.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.session import get_session_local
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.models.custom_images import CustomImageBuild
from app.services import argo, mirror

logger = logging.getLogger(__name__)
SECONDS_BETWEEN_PASSES = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def follow_job(db: Session, job: ImageMirrorJob) -> None:
    """Record the state of a running mirror job's workflow; at its end, the image's state too.

    A mirror that fails removes the image it was to add; a re-mirror that
    fails leaves the image as it was.
    """
    image = db.query(ContainerImage).filter(ContainerImage.id == job.image_id).first()
    try:
        state = argo.state(job.workflow)
    except argo.WorkflowGone as e:
        state = argo.WorkflowState("Error", str(e), job.logs or "")
    job.logs = state.log
    if not state.ended:
        db.commit()
        return
    mirrored = mirror.result(state.log) if state.phase == "Succeeded" else None
    job.completed_at = _now()
    if mirrored:
        job.status = "success"
        if image is not None:
            image.digest = mirrored["digest"]
            image.mirror_date = _now()
            # A new dict: a change inside the JSON column is not seen, a new value is
            image.image_metadata = {**(image.image_metadata or {}), "status": "active",
                                    "architectures": mirrored["architectures"]}
    else:
        job.status = "failed"
        job.error_message = (
            f"Workflow {job.workflow} {state.phase}: {state.message}" if state.phase != "Succeeded"
            else f"Workflow {job.workflow} succeeded but its log does not say what it pushed"
        )
        if image is not None and job.job_type == "mirror":
            db.delete(image)
    db.commit()
    logger.info("mirror job %s of %s ended %s", job.id, job.source_url, job.status)


def follow_build(db: Session, build: CustomImageBuild) -> None:
    """Record the state of a building image's workflow, and how it ended."""
    try:
        state = argo.state(build.workflow)
    except argo.WorkflowGone as e:
        state = argo.WorkflowState("Error", str(e), build.output or "")
    build.output = state.log
    if not state.ended:
        db.commit()
        return
    build.completed_at = _now()
    if state.phase == "Succeeded":
        build.status = "success"
        # A base image gets a minimal template for images that extend it.
        # registry.<domain>/library/name:latest -> library/name:latest
        if build.is_base:
            reference = build.registry_url.split("/", 1)[1]
            build.template = f"FROM {reference}\n\n# Extended from {build.name}\n# Add your customizations here\n\n"
    else:
        build.status = "failed"
        build.output = f"{state.log}\n\nWorkflow {build.workflow} {state.phase}: {state.message}\n"
    db.commit()
    logger.info("build of custom image %s ended %s", build.name, build.status)


def one_pass(db: Session) -> None:
    """Follow every running job and every building image once."""
    jobs = db.query(ImageMirrorJob).filter(ImageMirrorJob.status == "running", ImageMirrorJob.workflow.isnot(None))
    for job in jobs.all():
        follow_job(db, job)
    builds = db.query(CustomImageBuild).filter(CustomImageBuild.status == "building",
                                               CustomImageBuild.workflow.isnot(None))
    for build in builds.all():
        follow_build(db, build)


async def run() -> None:
    """Follow the workflows until cancelled. A pass that fails is logged and the next one runs."""
    while True:
        db = get_session_local()()
        try:
            await asyncio.to_thread(one_pass, db)
        except Exception:
            logger.exception("following the mirror jobs and builds failed; the next pass runs in %s s",
                             SECONDS_BETWEEN_PASSES)
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(SECONDS_BETWEEN_PASSES)
