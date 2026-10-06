#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The ref a template deploy generates from.

A cluster that follows a release branch deploys platform templates from
their release tags; a cluster that follows another branch deploys them from
that branch's head commit. A user's own template uses its newest tag on both.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import template_version as tv  # noqa: E402

PLATFORM = "https://github.com/thinkube/tkt-webapp-react-fastapi"
USERS = "https://github.com/cmxela/dentalpin"
HEAD = "c857ab79" + "0" * 32

TAGS = "\n".join([
    "a" * 40 + "\trefs/tags/v0.1.0",
    "b" * 40 + "\trefs/tags/v0.1.1",
    "c" * 40 + "\trefs/tags/v0.2.0",
    "d" * 40 + "\trefs/tags/v2.7.3",
])


@pytest.fixture
def git(monkeypatch):
    """ls-remote answers: tags for --tags, the head of main for --heads; the calls are recorded."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "--tags" in cmd:
            return SimpleNamespace(returncode=0, stdout=TAGS, stderr="")
        if "--heads" in cmd:
            ref = cmd[-1]
            out = f"{HEAD}\trefs/heads/main" if ref == "refs/heads/main" else ""
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("THINKUBE_PLATFORM_VERSION", "0.1")
    return calls


def test_a_cluster_on_a_release_branch_deploys_a_platform_template_from_its_tag(git, monkeypatch):
    monkeypatch.setenv("THINKUBE_BRANCH", "release-0.1")
    assert tv.template_ref(PLATFORM) == "v0.1.1"


def test_a_cluster_on_main_deploys_a_platform_template_from_the_head_of_main(git, monkeypatch):
    monkeypatch.setenv("THINKUBE_BRANCH", "main")
    assert tv.template_ref(PLATFORM) == HEAD
    assert not any("--tags" in c for c in git)


def test_a_users_template_uses_its_newest_tag_on_any_cluster(git, monkeypatch):
    for branch in ("main", "release-0.1"):
        monkeypatch.setenv("THINKUBE_BRANCH", branch)
        assert tv.template_ref(USERS) == "v2.7.3"


def test_a_branch_the_template_does_not_have_is_named(git, monkeypatch):
    monkeypatch.setenv("THINKUBE_BRANCH", "feature-x")
    with pytest.raises(tv.TemplateNotReleased, match="no branch 'feature-x'"):
        tv.template_ref(PLATFORM)


def test_without_thinkube_branch_a_platform_template_is_refused(git, monkeypatch):
    monkeypatch.delenv("THINKUBE_BRANCH", raising=False)
    with pytest.raises(RuntimeError, match="THINKUBE_BRANCH is not set"):
        tv.template_ref(PLATFORM)
