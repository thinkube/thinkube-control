# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A mirror request for an image reuses the pending job that registering it created."""

from app.api.harbor_images import _pending_mirror_job
from app.models.container_images import ContainerImage, ImageMirrorJob


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *clauses):
        return self

    def order_by(self, *clauses):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        assert model is ImageMirrorJob
        return FakeQuery(self.rows)


def test_the_pending_job_of_the_image_is_reused():
    image = ContainerImage(name="nemo-automodel", source_url="nvcr.io/nvidia/nemo-automodel:26.08")
    pending = ImageMirrorJob(job_type="mirror", status="pending", source_url=image.source_url)

    assert _pending_mirror_job(FakeDb([pending]), image) is pending


def test_an_image_without_a_pending_job_gets_none():
    image = ContainerImage(name="nemo-automodel", source_url="nvcr.io/nvidia/nemo-automodel:26.08")

    assert _pending_mirror_job(FakeDb([]), image) is None
