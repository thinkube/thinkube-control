# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Mirroring an image: the request, the workflow it submits, and how the job ends."""

import json
from datetime import datetime, timezone

import pytest

from app.api import images
from app.core.security import get_current_user
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.services import argo, follow, mirror

SOURCE = "nvcr.io/nvidia/nemo-automodel:26.08"
DESTINATION = "registry.test.com/library/nemo-automodel:26.08"


@pytest.fixture
def signed_in(client, mock_user):
    async def user():
        return mock_user
    client.app.dependency_overrides[get_current_user] = user
    yield client
    del client.app.dependency_overrides[get_current_user]


@pytest.fixture
def cluster(monkeypatch):
    """The cluster as the API sees it: two architectures, and workflows that are recorded, not run."""
    submitted = []

    def submit(workflow):
        submitted.append(workflow)
        return f"image-mirror-{len(submitted)}"

    monkeypatch.setattr(argo, "node_architectures", lambda: ["amd64", "arm64"])
    monkeypatch.setattr(argo, "submit", submit)
    return submitted


def workflow_ends(monkeypatch, phase, log, message=""):
    monkeypatch.setattr(argo, "state", lambda name: argo.WorkflowState(phase, message, log))


def mirrored_log(digest="sha256:abc", architectures=("amd64", "arm64")):
    return "+ buildah manifest push\n" + mirror.RESULT_MARK + json.dumps(
        {"digest": digest, "architectures": list(architectures)}) + "\n"


def test_the_name_is_the_last_path_element_and_the_tag_defaults_to_latest():
    assert images.parse_source(SOURCE) == ("nemo-automodel", "26.08")
    assert images.parse_source("docker.io/library/nginx") == ("nginx", "latest")
    assert images.parse_source("registry:5000/team/app:1.2") == ("app", "1.2")


@pytest.mark.parametrize("source", ["", "docker.io/library/", "docker.io/library/nginx@sha256:abc", "repo/:tag"])
def test_an_address_without_a_name_or_with_a_digest_is_refused(source):
    with pytest.raises(ValueError):
        images.parse_source(source)


def test_the_workflow_runs_the_step_as_the_image_builder_with_its_registry_login():
    workflow = mirror.workflow("job-1", "nemo-automodel", SOURCE, DESTINATION, ["amd64", "arm64"])
    assert workflow["metadata"]["namespace"] == "argo"
    assert workflow["metadata"]["labels"] == {mirror.LABEL: "job-1"}
    assert workflow["spec"]["serviceAccountName"] == "image-builder"
    container = workflow["spec"]["templates"][0]["container"]
    assert container["image"] == "registry.test.com/library/buildah:v1.43.4"
    assert container["command"][:2] == ["python3", "-c"]
    assert container["command"][3:] == [SOURCE, DESTINATION, "amd64,arm64"]
    assert {"name": "REGISTRY_AUTH_FILE", "value": "/registry-login/config.json"} in container["env"]
    assert workflow["spec"]["volumes"] == [{"name": "registry-login", "secret": {"secretName": "docker-config"}}]


def test_the_step_is_python_that_compiles():
    compile(mirror.STEP, "mirror-step", "exec")


def test_the_result_is_the_last_marked_line_of_the_log():
    assert mirror.result(mirrored_log("sha256:1") + mirrored_log("sha256:2"))["digest"] == "sha256:2"
    assert mirror.result("no such line\n") is None


def test_an_image_name_becomes_part_of_an_object_name():
    assert mirror.workflow_name_part("Nemo_AutoModel.v2") == "nemo-automodel-v2"
    assert mirror.workflow_name_part("___") == "image"


def test_mirroring_adds_the_image_and_a_running_job_with_its_workflow(signed_in, cluster, test_db):
    r = signed_in.post("/api/v1/images", json={"source_url": SOURCE, "description": "fine-tuning"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["image"]["destination_url"] == DESTINATION
    assert answer["image"]["category"] == "user"
    assert answer["image"]["metadata"]["status"] == "pending"
    assert answer["job"]["status"] == "running"
    assert answer["job"]["workflow"] == "image-mirror-1"
    assert answer["job"]["created_by"] == "testuser"
    command = cluster[0]["spec"]["templates"][0]["container"]["command"]
    assert command[3:] == [SOURCE, DESTINATION, "amd64,arm64"]
    assert test_db.query(ContainerImage).count() == 1


def test_an_image_already_in_the_list_is_refused(signed_in, cluster):
    assert signed_in.post("/api/v1/images", json={"source_url": SOURCE}).status_code == 200
    again = signed_in.post("/api/v1/images", json={"source_url": SOURCE})
    assert again.status_code == 409
    assert DESTINATION in again.json()["detail"]
    assert len(cluster) == 1


def test_an_address_that_is_not_an_image_is_400(signed_in, cluster):
    r = signed_in.post("/api/v1/images", json={"source_url": "docker.io/library/"})
    assert r.status_code == 400
    assert cluster == []


def test_a_workflow_that_cannot_be_submitted_leaves_no_image(signed_in, monkeypatch, test_db):
    monkeypatch.setattr(argo, "node_architectures", lambda: ["amd64"])

    def refuse(workflow):
        raise RuntimeError("workflows.argoproj.io is forbidden")

    monkeypatch.setattr(argo, "submit", refuse)
    with pytest.raises(RuntimeError):
        signed_in.post("/api/v1/images", json={"source_url": SOURCE})
    test_db.rollback()
    assert test_db.query(ContainerImage).count() == 0


def started(signed_in, cluster, test_db):
    job_id = signed_in.post("/api/v1/images", json={"source_url": SOURCE}).json()["job"]["id"]
    return test_db.query(ImageMirrorJob).filter(ImageMirrorJob.id == job_id).one()


def test_a_running_workflow_updates_the_log_and_nothing_else(signed_in, cluster, test_db, monkeypatch):
    job = started(signed_in, cluster, test_db)
    workflow_ends(monkeypatch, "Running", "+ buildah manifest create\n")
    follow.follow_job(test_db, job)
    seen = signed_in.get(f"/api/v1/jobs/{job.id}").json()
    assert seen["status"] == "running"
    assert seen["logs"] == "+ buildah manifest create\n"


def test_a_mirror_that_succeeds_records_the_digest_and_makes_the_image_active(signed_in, cluster, test_db,
                                                                             monkeypatch):
    job = started(signed_in, cluster, test_db)
    workflow_ends(monkeypatch, "Succeeded", mirrored_log("sha256:feed", ["arm64"]))
    follow.follow_job(test_db, job)
    assert job.status == "success"
    image = test_db.query(ContainerImage).one()
    assert image.digest == "sha256:feed"
    assert image.image_metadata["status"] == "active"
    assert image.image_metadata["architectures"] == ["arm64"]


def test_a_mirror_that_fails_removes_the_image_and_says_why(signed_in, cluster, test_db, monkeypatch):
    job = started(signed_in, cluster, test_db)
    workflow_ends(monkeypatch, "Failed", "nvcr.io/... provides ['s390x']; the cluster's nodes are ['amd64']\n",
                  "main: Error (exit code 1)")
    follow.follow_job(test_db, job)
    assert job.status == "failed"
    assert "Failed: main: Error (exit code 1)" in job.error_message
    assert "provides ['s390x']" in job.logs
    assert test_db.query(ContainerImage).count() == 0


def test_a_workflow_that_no_longer_exists_fails_the_job(signed_in, cluster, test_db, monkeypatch):
    job = started(signed_in, cluster, test_db)

    def gone(name):
        raise argo.WorkflowGone(f"the workflow argo/{name} no longer exists")

    monkeypatch.setattr(argo, "state", gone)
    follow.follow_job(test_db, job)
    assert job.status == "failed"
    assert "no longer exists" in job.error_message


def an_image(test_db, tag="latest", **fields):
    image = ContainerImage(
        name="nginx", registry="registry.test.com", repository="library/nginx", tag=tag,
        source_url=f"docker.io/library/nginx:{tag}", destination_url=f"registry.test.com/library/nginx:{tag}",
        category="user", protected=False, source="mirrored", is_base=False,
        mirror_date=datetime.now(timezone.utc), image_metadata={"status": "active"}, digest="sha256:old", **fields,
    )
    test_db.add(image)
    test_db.commit()
    return image


def test_only_latest_is_mirrored_again(signed_in, cluster, test_db):
    pinned = an_image(test_db, tag="1.27")
    r = signed_in.post(f"/api/v1/images/{pinned.id}/remirror")
    assert r.status_code == 400
    assert cluster == []


def test_a_remirror_that_fails_keeps_the_image(signed_in, cluster, test_db, monkeypatch):
    image = an_image(test_db)
    job_id = signed_in.post(f"/api/v1/images/{image.id}/remirror").json()["job"]["id"]
    job = test_db.query(ImageMirrorJob).filter(ImageMirrorJob.id == job_id).one()
    assert job.job_metadata == {"previous_digest": "sha256:old"}
    workflow_ends(monkeypatch, "Error", "", "pod deleted")
    follow.follow_job(test_db, job)
    assert job.status == "failed"
    assert test_db.query(ContainerImage).one().digest == "sha256:old"


def test_an_image_being_mirrored_is_not_mirrored_twice(signed_in, cluster, test_db):
    image = an_image(test_db)
    assert signed_in.post(f"/api/v1/images/{image.id}/remirror").status_code == 200
    again = signed_in.post(f"/api/v1/images/{image.id}/remirror")
    assert again.status_code == 409
    assert "already being mirrored" in again.json()["detail"]


def test_an_image_a_pod_runs_is_not_deleted(signed_in, test_db, monkeypatch):
    image = an_image(test_db)
    monkeypatch.setattr(images, "pods_using", lambda ref: ["apps/web-1"])
    r = signed_in.delete(f"/api/v1/images/{image.id}")
    assert r.status_code == 409
    assert "apps/web-1" in r.json()["detail"]
    monkeypatch.setattr(images, "pods_using", lambda ref: [])
    assert signed_in.delete(f"/api/v1/images/{image.id}").status_code == 200
    assert test_db.query(ContainerImage).count() == 0


def test_a_system_image_is_not_deleted(signed_in, test_db):
    image = an_image(test_db)
    image.category = "system"
    test_db.commit()
    assert signed_in.delete(f"/api/v1/images/{image.id}").status_code == 403


def test_a_reference_to_latest_is_also_the_reference_without_a_tag():
    assert images._image_names("registry.test.com/library/nginx:latest") == {
        "registry.test.com/library/nginx:latest", "registry.test.com/library/nginx"}
    assert images._image_names("registry.test.com/library/nginx:1.27") == {"registry.test.com/library/nginx:1.27"}


def test_the_notebook_images_are_those_whose_purpose_is_jupyter(client, test_db):
    an_image(test_db)
    notebook = ContainerImage(
        name="tk-jupyter-base", registry="registry.test.com", repository="library/tk-jupyter-base", tag="latest",
        destination_url="registry.test.com/library/tk-jupyter-base:latest", category="system", protected=True,
        source="built", is_base=False, mirror_date=datetime.now(timezone.utc),
        image_metadata={"purpose": "jupyter", "display_name": "Thinkube Jupyter"},
    )
    test_db.add(notebook)
    test_db.commit()
    listed = client.get("/api/v1/images/jupyter").json()
    assert [(i["name"], i["display_name"], i["default"]) for i in listed] == [
        ("tk-jupyter-base", "Thinkube Jupyter", True)]


def test_the_list_of_jobs_leaves_the_logs_out(signed_in, cluster, test_db, monkeypatch):
    job = started(signed_in, cluster, test_db)
    workflow_ends(monkeypatch, "Running", "a long log\n")
    follow.follow_job(test_db, job)
    listed = signed_in.get("/api/v1/jobs").json()
    assert listed["total"] == 1
    assert listed["jobs"][0]["logs"] is None
