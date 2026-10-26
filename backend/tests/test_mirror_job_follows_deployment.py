# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A mirror job follows its deployment to the end, however long the image takes."""

import asyncio

from app.api.harbor_images import _deployment_end


class FakeDeployment:
    status = "running"
    output = None


class FakeDb:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.refreshes = 0

    def refresh(self, deployment):
        self.refreshes += 1
        deployment.status = self.statuses.pop(0)


def test_the_job_waits_for_a_deployment_longer_than_ten_minutes():
    """At five seconds a check, 200 checks are over sixteen minutes: the job keeps following."""
    deployment = FakeDeployment()
    db = FakeDb(["running"] * 200 + ["success"])

    assert asyncio.run(_deployment_end(deployment, db, check_interval=0)) == "success"
    assert db.refreshes == 201


def test_a_failed_deployment_ends_the_wait():
    deployment = FakeDeployment()
    db = FakeDb(["running", "failed"])

    assert asyncio.run(_deployment_end(deployment, db, check_interval=0)) == "failed"
    assert db.refreshes == 2


def test_an_ended_deployment_is_not_waited_for():
    deployment = FakeDeployment()
    deployment.status = "cancelled"

    assert asyncio.run(_deployment_end(deployment, FakeDb([]), check_interval=0)) == "cancelled"
