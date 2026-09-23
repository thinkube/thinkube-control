# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The custom image build endpoint answers before the build runs, and the build still runs."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.api.custom_images as ci
from app.api.custom_images import BuildImageRequest
from app.services import detached


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter_by(self, **kwargs):
        return self

    def filter(self, *args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.commits = 0

    def query(self, model):
        return FakeQuery(self.rows)

    def commit(self):
        self.commits += 1


def image(**fields):
    row = dict(id=uuid.uuid4(), name="jp-test", status="pending", output=None, build_config=None,
               started_at=None, completed_at=None, is_base=False, registry_url=None, template=None,
               dockerfile_path="/nonexistent/Dockerfile")
    row.update(fields)
    return SimpleNamespace(**row)


def test_build_returns_while_the_build_is_still_running(monkeypatch):
    build = image()
    db = FakeDB(build)
    build_started = asyncio.Event()
    build_may_finish = asyncio.Event()
    finished = []

    async def fake_build(build_id):
        build_started.set()
        await build_may_finish.wait()
        finished.append(build_id)

    monkeypatch.setattr(ci, "_execute_custom_image_build", fake_build)

    async def scenario():
        response = await ci.build_custom_image(build.id, BuildImageRequest(), db=db, current_user={})
        assert response.status == "building"
        assert response.poll_url == f"/custom-images/{build.id}"
        await asyncio.wait_for(build_started.wait(), 1)
        assert finished == []
        assert detached.running(f"image-build:{build.id}")
        build_may_finish.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert finished == [str(build.id)]
        assert not detached.running(f"image-build:{build.id}")

    asyncio.run(scenario())
    assert build.status == "building"
    assert build.started_at is not None
    assert db.commits == 1


def test_a_build_that_is_running_is_not_started_twice(monkeypatch):
    build = image(status="building")
    started = []

    async def fake_build(build_id):
        started.append(build_id)

    monkeypatch.setattr(ci, "_execute_custom_image_build", fake_build)

    async def scenario():
        with pytest.raises(HTTPException) as refused:
            await ci.build_custom_image(build.id, BuildImageRequest(), db=FakeDB(build), current_user={})
        assert refused.value.status_code == 400

    asyncio.run(scenario())
    assert started == []


def test_the_log_is_named_before_the_build_runs_and_a_missing_dockerfile_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, "BUILD_LOG_DIR", tmp_path)
    build = image()
    db = FakeDB(build)

    result = asyncio.run(ci._run_image_build(build, db))

    assert result == {"return_code": 1}
    assert build.output.startswith(str(tmp_path / "jp-test"))
    assert db.commits == 1
    log = open(build.output).read()
    assert "Dockerfile not found: /nonexistent/Dockerfile" in log


def test_a_build_with_no_task_behind_it_is_marked_failed():
    orphan = image(status="building", output="/tmp/thinkube-builds/jp-test/build-1.log")
    live = image(status="building")
    db = FakeDB(orphan, live)

    marked = ci.mark_orphaned_image_builds(db, still_running=lambda build_id: build_id == str(live.id))

    assert marked == 1
    assert orphan.status == "failed"
    assert "restarted while this image was building" in orphan.output
    assert live.status == "building"
    assert db.commits == 1
