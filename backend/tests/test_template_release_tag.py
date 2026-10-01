#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A template in the thinkube organization deploys from its newest tag of the
platform's MAJOR.MINOR; any other template deploys from its newest tag."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import template_version as tv  # noqa: E402

TAGS = ["v0.1.0", "v0.1.7", "v0.1.12", "v0.2.0", "v1.0.0", "latest", "v0.3"]


@pytest.fixture(autouse=True)
def tags(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("THINKUBE_PLATFORM_VERSION", "0.1")
    listing = {"tags": TAGS}

    def run(cmd, **kwargs):
        stdout = "".join(f"abc123\trefs/tags/{tag}\n" for tag in listing["tags"])
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(tv.subprocess, "run", run)
    return listing


def test_a_platform_template_uses_the_newest_tag_of_the_platform_version():
    assert tv.latest_release_tag("https://github.com/thinkube/tkt-webapp") == "v0.1.12"


def test_a_platform_template_follows_the_platform_version(monkeypatch):
    monkeypatch.setenv("THINKUBE_PLATFORM_VERSION", "0.2")
    assert tv.latest_release_tag("https://github.com/thinkube/tkt-webapp") == "v0.2.0"


def test_a_platform_template_without_a_tag_of_the_platform_version_is_an_error(monkeypatch):
    monkeypatch.setenv("THINKUBE_PLATFORM_VERSION", "0.4")
    with pytest.raises(tv.TemplateNotReleased, match=r"github.com/thinkube/tkt-webapp.*0\.4"):
        tv.latest_release_tag("https://github.com/thinkube/tkt-webapp")


def test_a_platform_template_without_the_platform_version_is_an_error(monkeypatch):
    monkeypatch.delenv("THINKUBE_PLATFORM_VERSION")
    with pytest.raises(RuntimeError, match="THINKUBE_PLATFORM_VERSION"):
        tv.latest_release_tag("https://github.com/thinkube/tkt-webapp")


def test_a_platform_version_that_is_not_major_minor_is_an_error(monkeypatch):
    monkeypatch.setenv("THINKUBE_PLATFORM_VERSION", "0.1.0")
    with pytest.raises(RuntimeError, match="MAJOR.MINOR"):
        tv.latest_release_tag("https://github.com/thinkube/tkt-webapp")


def test_a_user_template_uses_its_newest_tag(monkeypatch):
    monkeypatch.delenv("THINKUBE_PLATFORM_VERSION")
    assert tv.latest_release_tag("https://github.com/someone/my-template") == "v1.0.0"
