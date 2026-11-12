#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A deploy from a folder of a repository copies that folder, and nothing else, over the checkout."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from template_folder import (  # noqa: E402
    application_folders,
    fetch_folder,
    folder_name,
    folder_segments,
    removable,
    replace_checkout,
    source_refusal,
)

CONTROL = "https://github.com/thinkube/thinkube-control"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout


def commit(repo: Path, files: dict, message: str = "change") -> str:
    for path, content in files.items():
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    commit(repo, {
        "README.md": "the repository\n",
        "extensions/control-storage/thinkube.yaml": "kind: ThinkubeDeployment\n",
        "extensions/control-storage/backend/app/main.py": "app = 1\n",
        "extensions/forge/thinkube.yaml": "forge\n",
    })
    return repo


def test_the_application_has_the_folders_name():
    assert folder_name("extensions/control-storage") == "control-storage"
    assert folder_segments("extensions/control-storage") == ["extensions", "control-storage"]


@pytest.mark.parametrize("path", ["", "/extensions/forge", "extensions/forge/", "extensions/../forge", ".", "a//b", "a b"])
def test_a_path_that_is_not_a_plain_folder_is_refused(path):
    with pytest.raises(ValueError, match="not a folder of the repository"):
        folder_segments(path)


def test_only_the_folder_is_copied(source, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    archive = fetch_folder(str(source), "main", "extensions/control-storage", str(work), ENV)
    checkout = tmp_path / "checkout"
    replace_checkout(str(checkout), archive)
    assert (checkout / "thinkube.yaml").read_text() == "kind: ThinkubeDeployment\n"
    assert (checkout / "backend/app/main.py").read_text() == "app = 1\n"
    assert not (checkout / "README.md").exists()
    assert not (checkout / "extensions").exists()


def test_the_folder_is_read_at_the_ref(source, tmp_path):
    first = git(source, "rev-parse", "HEAD").strip()
    commit(source, {"extensions/control-storage/thinkube.yaml": "second\n"})
    work = tmp_path / "work"
    work.mkdir()
    git(source, "config", "uploadpack.allowReachableSHA1InWant", "true")
    archive = fetch_folder(str(source), first, "extensions/control-storage", str(work), ENV)
    checkout = tmp_path / "checkout"
    replace_checkout(str(checkout), archive)
    assert (checkout / "thinkube.yaml").read_text() == "kind: ThinkubeDeployment\n"


def test_a_folder_the_repository_does_not_have_is_named(source, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError, match="has no folder 'extensions/models' at main"):
        fetch_folder(str(source), "main", "extensions/models", str(work), ENV)


def test_a_file_is_not_a_folder(source, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError, match="has no folder 'README.md'"):
        fetch_folder(str(source), "main", "README.md", str(work), ENV)


def test_a_file_removed_from_the_folder_leaves_the_checkout_and_the_builds_files_stay(source, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-b", "main")
    commit(checkout, {
        "thinkube.yaml": "old\n",
        "backend/app/removed.py": "gone from the folder\n",
        "k8s/.argocd-source-control-storage.yaml": "image tags\n",
        "k8s/deployments.yaml": "generated\n",
    })
    (checkout / "untracked.txt").write_text("not the deploy's\n")

    work = tmp_path / "work"
    work.mkdir()
    replace_checkout(str(checkout), fetch_folder(str(source), "main", "extensions/control-storage", str(work), ENV))

    assert (checkout / "thinkube.yaml").read_text() == "kind: ThinkubeDeployment\n"
    assert not (checkout / "backend/app/removed.py").exists()
    assert (checkout / "k8s/.argocd-source-control-storage.yaml").read_text() == "image tags\n"
    assert not (checkout / "k8s/deployments.yaml").exists()
    assert (checkout / "untracked.txt").exists()


def test_the_applications_of_a_folder_are_its_folders_with_a_thinkube_yaml(source, tmp_path):
    commit(source, {"extensions/notes/README.md": "no application here\n", "extensions/list.txt": "a file\n"})
    work = tmp_path / "work"
    work.mkdir()
    assert application_folders(str(source), "main", "extensions", str(work), ENV) == ["control-storage", "forge"]


def test_a_repository_without_the_parent_folder_is_named(source, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError, match="has no folder 'parts' at main"):
        application_folders(str(source), "main", "parts", str(work), ENV)


def test_only_the_builds_image_tags_are_kept():
    assert removable(["thinkube.yaml", "k8s/.argocd-source-forge.yaml", "k8s/sub/.argocd-source-x.yaml"]) == [
        "thinkube.yaml", "k8s/sub/.argocd-source-x.yaml",
    ]


def test_an_app_deployed_from_a_folder_is_deployed_from_that_folder_again():
    assert source_refusal("forge", CONTROL, "extensions/forge", CONTROL + "/", "extensions/forge") is None


def test_an_app_deployed_from_a_folder_refuses_another_source():
    template = "https://github.com/thinkube/tkt-webapp-react-fastapi"
    refused = source_refusal("forge", CONTROL, "extensions/forge", template, None)
    assert "forge is deployed from the folder extensions/forge" in refused
    assert f"template_url {CONTROL}, template_path extensions/forge" in refused
    assert source_refusal("forge", CONTROL, "extensions/forge", CONTROL, "extensions/datasets")


def test_an_app_deployed_from_a_template_may_move_to_a_folder():
    template = "https://github.com/thinkube/tkt-webapp-react-fastapi"
    assert source_refusal("forge", template, None, CONTROL, "extensions/forge") is None
    assert source_refusal("forge", template, None, template, None) is None
