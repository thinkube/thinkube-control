# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Follows the running mirror jobs: every POLL_SECONDS each one is set from its
Argo workflow, so a mirror ends in the list without anyone asking for it."""

import asyncio
import logging

from app.db.session import get_session_local
from app.models.model_mirrors import ModelMirrorJob
from app.services.model_downloader import ModelDownloaderService

logger = logging.getLogger(__name__)

POLL_SECONDS = 10


def follow_once() -> None:
    db = get_session_local()()
    try:
        jobs = db.query(ModelMirrorJob).filter(
            ModelMirrorJob.status.in_(["pending", "running"]), ModelMirrorJob.workflow_name.isnot(None)
        ).all()
        if not jobs:
            return
        service = ModelDownloaderService()
        for job in jobs:
            try:
                service.sync_mirror_job(job)
            except Exception:
                # The job keeps its status; the next pass asks Argo again
                logger.exception(f"The workflow {job.workflow_name} of the mirror of {job.model_id} was not read")
        db.commit()
    finally:
        db.close()


async def run() -> None:
    while True:
        try:
            await asyncio.to_thread(follow_once)
        except Exception:
            logger.exception("The mirror jobs were not followed in this pass")
        await asyncio.sleep(POLL_SECONDS)
