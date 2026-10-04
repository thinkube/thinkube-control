#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A deploy's checkout is in its place from the start, and a component's pushed commits are never replaced unseen."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from component_checkout import (  # noqa: E402
    Commit,
    check_declared_type,
    checkout_path,
    developer_commits,
    parse_log,
    refusal,
)

DEPLOY = "Deploy vllm to thinkube.com"
BUILD = "build: automatic update of vllm to 393b4023"


def test_a_component_is_checked_out_under_components_and_an_app_under_apps():
    assert checkout_path("/h/apps", "/h/components", "vllm", "component") == "/h/components/vllm"
    assert checkout_path("/h/apps", "/h/components", "notes", "user_app") == "/h/apps/notes"


def test_a_deploy_without_its_type_is_refused():
    with pytest.raises(ValueError, match="deployment_type None"):
        checkout_path("/h/apps", "/h/components", "vllm", None)


def test_a_thinkube_yaml_that_disagrees_with_the_catalog_is_refused():
    check_declared_type("component", True, "vllm")
    check_declared_type("user_app", False, "notes")
    with pytest.raises(ValueError, match="does not list it as one"):
        check_declared_type("user_app", True, "vllm")
    with pytest.raises(ValueError, match="does not declare"):
        check_declared_type("component", False, "vllm")


def test_deploy_and_build_commits_are_not_developer_changes():
    log = [Commit("c3", "system", BUILD), Commit("c2", "system", DEPLOY), Commit("c1", "tkadmin", "Add DFlash")]
    assert developer_commits(log, "vllm", "thinkube.com") == []


def test_commits_after_the_last_deploy_are_developer_changes_and_older_ones_are_not():
    log = [
        Commit("c5", "system", BUILD),
        Commit("c4", "tkadmin", "Raise the batched-token budget for DFlash"),
        Commit("c3", "tkadmin", "Pass the speculative config"),
        Commit("c2", "system", DEPLOY),
        Commit("c1", "tkadmin", "an older change a deploy already replaced"),
    ]
    found = developer_commits(log, "vllm", "thinkube.com")
    assert [c.sha for c in found] == ["c4", "c3"]
    message = refusal("vllm", found)
    assert "2 commit(s)" in message and "Pass the speculative config" in message
    assert "_replace_developer_commits" in message


def test_a_repository_never_deployed_from_the_template_counts_every_other_commit():
    log = [Commit("c2", "system", BUILD), Commit("c1", "tkadmin", "Initial import")]
    assert [c.sha for c in developer_commits(log, "vllm", "thinkube.com")] == ["c1"]


def test_the_history_is_read_from_real_git_output(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.name", "tkadmin")
    git("config", "user.email", "tkadmin@thinkube.com")
    for subject in (DEPLOY, BUILD, "Add DFlash\n\nbody\twith a tab"):
        git("commit", "-q", "--allow-empty", "-m", subject)
    log = parse_log(git("log", "--format=%H%x09%an%x09%s"))
    assert [c.subject for c in log] == ["Add DFlash", BUILD, DEPLOY]
    assert [c.subject for c in developer_commits(log, "vllm", "thinkube.com")] == ["Add DFlash"]
