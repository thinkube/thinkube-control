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


def test_the_image_directory_travels_as_text_artifacts(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\nCOPY conf/app.conf /app.conf\n")
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "app.conf").write_text("x=1\n")

    artifacts = ci._context_artifacts(tmp_path)

    assert [(a["path"], a["raw"]["data"]) for a in artifacts] == [
        ("/context/Dockerfile", "FROM scratch\nCOPY conf/app.conf /app.conf\n"),
        ("/context/conf/app.conf", "x=1\n"),
    ]


def test_a_binary_file_or_a_large_directory_is_refused_by_name(tmp_path, monkeypatch):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="blob.bin is not a text file"):
        ci._context_artifacts(tmp_path)

    (tmp_path / "blob.bin").unlink()
    monkeypatch.setattr(ci, "CONTEXT_LIMIT_BYTES", 5)
    with pytest.raises(ValueError, match="holds 13 bytes"):
        ci._context_artifacts(tmp_path)


def test_the_workflow_builds_with_buildah_on_the_given_architecture():
    build = image(dockerfile_path="/home/thinkube/dockerfiles/custom/jp-test/Dockerfile",
                  build_config={"build_args": {"PY": "3.12 slim"}})
    url = "registry.thinkube.com/library/jp-test:latest"

    workflow = ci._build_workflow(build, url, "arm64", [{"name": "file-0", "path": "/context/Dockerfile", "raw": {"data": "FROM scratch"}}])

    spec = workflow["spec"]
    step = spec["templates"][0]
    container = step["container"]
    assert workflow["metadata"]["labels"] == {"thinkube.io/custom-image-build": str(build.id)}
    assert spec["serviceAccountName"] == "image-builder"
    assert step["nodeSelector"] == {"kubernetes.io/arch": "arm64"}
    assert container["image"] == "registry.thinkube.com/library/buildah:v1.43.4"
    assert container["securityContext"]["capabilities"] == {"add": ["SYS_ADMIN"]}
    assert "privileged" not in container["securityContext"]
    script = container["args"][0]
    assert script.index("getent hosts registry.thinkube.com") < script.index("buildah build")
    assert "--build-arg 'PY=3.12 slim'" in script
    assert "--cache-from registry.thinkube.com/library/jp-test/cache" in script
    assert "-f /context/Dockerfile -t registry.thinkube.com/library/jp-test:latest /context" in script
    assert "buildah push --storage-driver overlay --retry 3 registry.thinkube.com/library/jp-test:latest" in script


def _runner(monkeypatch, tmp_path, states):
    monkeypatch.setattr(ci, "BUILD_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(ci, "POLL_SECONDS", 0)
    submitted = []
    monkeypatch.setattr(ci, "_submit_workflow", lambda wf: submitted.append(wf) or "custom-image-jp-test-abc")
    replies = iter(states)
    monkeypatch.setattr(ci, "_workflow_state", lambda name: next(replies))
    context = tmp_path / "ctx"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\n")
    return image(dockerfile_path=str(context / "Dockerfile")), submitted


def test_a_workflow_that_succeeds_names_the_image_and_keeps_the_pod_log(monkeypatch, tmp_path):
    build, submitted = _runner(monkeypatch, tmp_path, [
        ("Running", "", "STEP 1/1: FROM scratch\n"),
        ("Succeeded", "", "STEP 1/1: FROM scratch\nWriting manifest\n"),
    ])

    result = asyncio.run(ci._run_image_build(build, FakeDB(build)))

    assert result["return_code"] == 0
    assert result["registry_url"].endswith("/library/jp-test:latest")
    assert len(submitted) == 1
    log = open(build.output).read()
    assert "Workflow: argo/custom-image-jp-test-abc" in log
    assert "Writing manifest" in log and "BUILD COMPLETED SUCCESSFULLY" in log


def test_a_workflow_that_fails_says_why_in_the_log(monkeypatch, tmp_path):
    build, _ = _runner(monkeypatch, tmp_path, [("Failed", "child failed", "Error: no such image\n")])

    result = asyncio.run(ci._run_image_build(build, FakeDB(build)))

    assert result == {"return_code": 1}
    log = open(build.output).read()
    assert "Error: no such image" in log and "Workflow Failed: child failed" in log


def test_deleting_an_image_removes_its_build_workflows(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, "BUILD_LOG_DIR", tmp_path)
    deleted = []
    monkeypatch.setattr(ci, "_delete_workflows", lambda build_id: deleted.append(build_id))
    build = image(status="success", dockerfile_path=str(tmp_path / "gone" / "Dockerfile"))

    class DeletingDB(FakeDB):
        def delete(self, row):
            self.rows.remove(row)

    db = DeletingDB(build)
    asyncio.run(ci.delete_custom_image(build.id, db=db, current_user={}))

    assert deleted == [str(build.id)]
    assert db.rows == []
