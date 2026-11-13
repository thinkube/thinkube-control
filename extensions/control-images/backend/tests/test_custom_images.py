# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Custom images: the folder a new one gets, the build it starts, and how the build ends."""

from pathlib import Path

import pytest

from app.core.security import get_current_user
from app.models.custom_images import CustomImageBuild
from app.services import argo, builds, dockerfiles, follow


@pytest.fixture
def signed_in(client, mock_user):
    async def user():
        return mock_user
    client.app.dependency_overrides[get_current_user] = user
    yield client
    del client.app.dependency_overrides[get_current_user]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """The dockerfiles folder, in a temporary place; ownership changes are recorded, not made."""
    given = []
    monkeypatch.setattr(dockerfiles, "ROOT", tmp_path)
    monkeypatch.setattr(dockerfiles.os, "chown", lambda path, uid, gid: given.append((Path(path), uid, gid)))
    return given


@pytest.fixture
def submitted(monkeypatch):
    workflows = []

    def submit(workflow):
        workflows.append(workflow)
        return "custom-image-web-abc12"

    monkeypatch.setattr(argo, "submit", submit)
    return workflows


def create(signed_in, **fields):
    body = {"name": "web", "build_config": {"base_image": "python:3.12-slim", "description": "A web app"}, **fields}
    return signed_in.post("/api/v1/custom-images", json=body)


def test_a_new_image_gets_its_folder_owned_by_the_ide_user(signed_in, folder, tmp_path):
    r = create(signed_in)
    assert r.status_code == 200, r.text
    image = r.json()
    assert image["status"] == "not_built"
    assert image["dockerfile_path"] == "/home/thinkube/dockerfiles/custom/web/Dockerfile"
    assert image["scope"] == "webapp"
    dockerfile = tmp_path / "custom/web/Dockerfile"
    assert dockerfile.read_text().startswith("FROM library/python:3.12-slim")
    assert (tmp_path / "custom/web/context/.gitkeep").exists()
    assert (dockerfile, dockerfiles.IDE_USER, dockerfiles.IDE_USER) in folder
    assert (tmp_path, dockerfiles.IDE_USER, dockerfiles.IDE_USER) in folder


def test_a_new_image_needs_something_to_start_from(signed_in, folder):
    r = signed_in.post("/api/v1/custom-images", json={"name": "web"})
    assert r.status_code == 400
    assert "base_image" in r.json()["detail"]


def test_a_name_is_one_folder_name(signed_in, folder):
    assert create(signed_in, name="a/b").status_code == 400
    assert create(signed_in, name="..").status_code == 400


def test_an_unknown_base_image_starts_from_its_from_line(signed_in, folder, tmp_path):
    create(signed_in, build_config={"base_image": "library/tk-jupyter-base:latest"})
    assert (tmp_path / "custom/web/Dockerfile").read_text().startswith("FROM library/tk-jupyter-base:latest\n")


def test_an_image_that_extends_another_starts_from_its_dockerfile(signed_in, folder, tmp_path):
    parent = create(signed_in).json()
    (tmp_path / "custom/web/Dockerfile").write_text("FROM library/python:3.12-slim\nRUN echo parent\n")
    child = create(signed_in, name="web-plus", build_config={}, parent_image_id=parent["id"])
    assert child.status_code == 200, child.text
    assert child.json()["parent_image_id"] == parent["id"]
    assert "RUN echo parent" in (tmp_path / "custom/web-plus/Dockerfile").read_text()


def test_a_build_sends_the_folder_and_records_its_workflow(signed_in, folder, submitted, tmp_path, test_db):
    image = create(signed_in).json()
    (tmp_path / "custom/web/context/app.py").write_text("print('hi')\n")
    r = signed_in.post(f"/api/v1/custom-images/{image['id']}/build", json={"build_args": {"V": "1"}})
    assert r.status_code == 200, r.text
    assert r.json()["workflow"] == "custom-image-web-abc12"
    workflow = submitted[0]
    template = workflow["spec"]["templates"][0]
    paths = sorted(a["path"] for a in template["inputs"]["artifacts"])
    assert paths == ["/context/Dockerfile", "/context/README.md", "/context/context/.gitkeep",
                     "/context/context/app.py"]
    script = template["container"]["args"][0]
    assert "--build-arg V=1" in script
    assert "-t registry.test.com/library/web:latest /context" in script
    assert workflow["metadata"]["labels"] == {builds.LABEL: image["id"]}
    build = test_db.query(CustomImageBuild).one()
    assert (build.status, build.workflow) == ("building", "custom-image-web-abc12")
    assert signed_in.post(f"/api/v1/custom-images/{image['id']}/build", json={}).status_code == 400


def test_a_file_that_is_not_text_stops_the_build_before_it_starts(signed_in, folder, submitted, tmp_path):
    image = create(signed_in).json()
    (tmp_path / "custom/web/context/model.bin").write_bytes(b"\xff\xfe\x00\x80")
    r = signed_in.post(f"/api/v1/custom-images/{image['id']}/build", json={})
    assert r.status_code == 400
    assert "context/model.bin is not a text file" in r.json()["detail"]
    assert submitted == []


def test_a_folder_over_the_limit_stops_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(dockerfiles, "ROOT", tmp_path)
    (tmp_path / "big.txt").write_text("x" * (dockerfiles.CONTEXT_LIMIT_BYTES + 1))
    with pytest.raises(ValueError, match="can hold 921600 at most"):
        dockerfiles.context_artifacts(tmp_path)


def building(signed_in, test_db, **fields):
    image = create(signed_in, **fields).json()
    signed_in.post(f"/api/v1/custom-images/{image['id']}/build", json={})
    return test_db.query(CustomImageBuild).filter(CustomImageBuild.name == image["name"]).one()


def test_the_log_is_readable_while_the_image_builds(signed_in, folder, submitted, test_db, monkeypatch):
    build = building(signed_in, test_db)
    monkeypatch.setattr(argo, "state", lambda name: argo.WorkflowState("Running", "", "STEP 1/4: FROM\n"))
    follow.follow_build(test_db, build)
    seen = signed_in.get(f"/api/v1/custom-images/{build.id}/log").json()
    assert seen == {"status": "building", "workflow": "custom-image-web-abc12", "log": "STEP 1/4: FROM\n"}
    assert signed_in.get(f"/api/v1/custom-images/{build.id}").json()["output"] is None


def test_a_base_image_that_builds_gets_a_template(signed_in, folder, submitted, test_db, monkeypatch):
    build = building(signed_in, test_db, is_base=True)
    monkeypatch.setattr(argo, "state", lambda name: argo.WorkflowState("Succeeded", "", "pushed\n"))
    follow.follow_build(test_db, build)
    assert build.status == "success"
    assert build.registry_url == "registry.test.com/library/web:latest"
    assert build.template.startswith("FROM library/web:latest\n")


def test_a_build_that_fails_says_how_the_workflow_ended(signed_in, folder, submitted, test_db, monkeypatch):
    build = building(signed_in, test_db)
    monkeypatch.setattr(argo, "state", lambda name: argo.WorkflowState("Failed", "exit code 125", "error: x\n"))
    follow.follow_build(test_db, build)
    assert build.status == "failed"
    assert "error: x" in build.output
    assert "Failed: exit code 125" in build.output


def test_deleting_an_image_removes_its_folder_and_its_workflows(signed_in, folder, tmp_path, monkeypatch):
    deleted = []
    monkeypatch.setattr(argo, "delete_labelled", lambda label, value: deleted.append((label, value)))
    image = create(signed_in).json()
    assert signed_in.delete(f"/api/v1/custom-images/{image['id']}").status_code == 200
    assert not (tmp_path / "custom/web").exists()
    assert deleted == [(builds.LABEL, image["id"])]


def test_the_base_images_are_the_fixed_ones_and_those_marked_as_base(signed_in, folder, submitted, test_db,
                                                                    monkeypatch):
    build = building(signed_in, test_db, is_base=True)
    monkeypatch.setattr(argo, "state", lambda name: argo.WorkflowState("Succeeded", "", ""))
    follow.follow_build(test_db, build)
    listed = signed_in.get("/api/v1/custom-images/base-registry").json()["images"]
    assert {(i["name"], i["source"]) for i in listed} == {
        ("python:3.12-slim", "predefined"), ("ubuntu:24.04", "predefined"), ("web", "built")}
