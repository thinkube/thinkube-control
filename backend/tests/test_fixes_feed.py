# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The news and fixes feed is refused whole when one entry is wrong; a fix's
status comes from the installed version; applying a fix only moves the thinkube
checkout forward."""

import asyncio
import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import fixes_feed as ff

COMMIT = "a" * 40

FEED = {
    "news": [
        {"id": "thinkube-0-1-0", "date": "2026-10-20", "title": "Thinkube 0.1.0", "body": "Released."},
    ],
    "fixes": [
        {
            "id": "valkey-password",
            "date": "2026-10-21",
            "title": "Valkey test uses the password",
            "body": "The test now authenticates.",
            "severity": "bug",
            "component": "valkey",
            "kind": "optional",
            "fixed_in": "0.1.1",
            "commit": COMMIT,
            "playbook": "ansible/40_thinkube/optional/valkey/00_install.yaml",
        },
    ],
}


def feed(**changes):
    data = copy.deepcopy(FEED)
    data["fixes"][0].update(changes)
    return data


def test_a_valid_feed_is_accepted():
    assert ff.validate_feed(copy.deepcopy(FEED)) == FEED


@pytest.mark.parametrize(
    "data, message",
    [
        (feed(severity="minor"), "severity"),
        (feed(kind="user"), "kind"),
        (feed(fixed_in="0.1"), "fixed_in"),
        (feed(commit="abc123"), "commit"),
        (feed(playbook="/etc/passwd"), "playbook"),
        (feed(playbook="ansible/40_thinkube/optional/../../../x.yaml"), "playbook"),
        (feed(playbook="ansible/40_thinkube/core/valkey/00_install.yaml"), "playbook"),
        (feed(id="thinkube-0-1-0"), "used twice"),
        (feed(title=""), "title"),
        ({"news": []}, "fixes"),
        ({"news": [], "fixes": [], "extra": []}, "unknown keys"),
    ],
)
def test_one_wrong_entry_refuses_the_feed(data, message):
    with pytest.raises(ff.FeedError, match=message):
        ff.validate_feed(data)


def test_a_missing_field_is_named():
    data = copy.deepcopy(FEED)
    del data["fixes"][0]["commit"]
    with pytest.raises(ff.FeedError, match="has no commit"):
        ff.validate_feed(data)


def test_the_feed_file_follows_the_platform_version(monkeypatch):
    monkeypatch.setenv("THINKUBE_PLATFORM_VERSION", "0.1")
    assert ff.feed_path() == "releases/0.1.json"
    monkeypatch.delenv("THINKUBE_PLATFORM_VERSION")
    with pytest.raises(ff.FeedError, match="THINKUBE_PLATFORM_VERSION is not set"):
        ff.feed_path()


def feed_repo(tmp_path, data):
    repo = tmp_path / "feed-upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "releases").mkdir()
    (repo / "releases" / "0.1.json").write_text(json.dumps(data))
    git(repo, "add", "releases/0.1.json")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feed")
    return repo


def test_the_feed_is_cloned_then_fetched_and_read_from_the_fetched_commit(tmp_path):
    upstream = feed_repo(tmp_path, FEED)
    clone = tmp_path / "fixes" / "thinkube-fixes"
    feed, commit = ff.fetch_feed("releases/0.1.json", checkout=clone, repo_url=str(upstream))
    assert feed == FEED
    assert commit == git(upstream, "rev-parse", "HEAD")
    assert (clone / ".git").exists()

    newer = copy.deepcopy(FEED)
    newer["news"][0]["title"] = "Thinkube 0.1.1"
    second = commit_file(upstream, "releases/0.1.json", json.dumps(newer))
    feed, commit = ff.fetch_feed("releases/0.1.json", checkout=clone, repo_url=str(upstream))
    assert feed["news"][0]["title"] == "Thinkube 0.1.1"
    assert commit == second


def test_a_release_with_no_feed_file_is_named(tmp_path):
    upstream = feed_repo(tmp_path, FEED)
    with pytest.raises(ff.FeedError, match="releases/0.2.json does not exist"):
        ff.fetch_feed("releases/0.2.json", checkout=tmp_path / "clone", repo_url=str(upstream))


def test_a_wrong_feed_in_the_repository_is_refused_whole(tmp_path):
    upstream = feed_repo(tmp_path, feed(severity="minor"))
    with pytest.raises(ff.FeedError, match="severity"):
        ff.fetch_feed("releases/0.1.json", checkout=tmp_path / "clone", repo_url=str(upstream))


@pytest.mark.parametrize(
    "installed, status",
    [
        ({}, ff.NOT_INSTALLED),
        ({"valkey": "0.1.0"}, ff.AVAILABLE),
        ({"valkey": "0.1.1"}, ff.APPLIED),
        ({"valkey": "0.1.10"}, ff.APPLIED),
    ],
)
def test_status_comes_from_the_installed_version(installed, status):
    assert ff.fix_status(FEED["fixes"][0], installed) == status


def test_a_fix_of_thinkube_control_is_applied_from_the_ide():
    fix = feed(component="thinkube-control", kind="core",
               playbook="ansible/40_thinkube/core/thinkube-control/12_deploy.yaml")["fixes"][0]
    assert ff.fix_status(fix, {"thinkube-control": "0.1.0"}) == ff.APPLY_FROM_IDE
    assert ff.fix_status(fix, {"thinkube-control": "0.1.1"}) == ff.APPLIED


def test_installed_versions_come_from_the_discovery_labels():
    def cm(labels):
        return SimpleNamespace(metadata=SimpleNamespace(labels=labels))

    items = [
        cm({"thinkube.io/service-name": "valkey", "thinkube.io/component-version": "0.1.2"}),
        cm({"thinkube.io/service-name": "app", "thinkube.io/component-version": "{{ x }}"}),
        cm({"thinkube.io/service-name": "nolabel"}),
    ]
    seen = {}

    def list_configmaps(label_selector):
        seen["selector"] = label_selector
        return SimpleNamespace(items=items)

    assert ff.installed_versions(list_configmaps) == {"valkey": "0.1.2"}
    assert seen["selector"] == "thinkube.io/managed=true"


# The checkout step, against real git repositories in a temporary directory.

def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_file(repo, name, text):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path, monkeypatch):
    monkeypatch.setenv("THINKUBE_BRANCH", "release-0.1")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "release-0.1")
    commit_file(upstream, "VERSION", "0.1.0")
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", "-b", "release-0.1", str(upstream), str(checkout)], check=True)
    return upstream, checkout


def run_update(commit, checkout, upstream):
    lines = []
    asyncio.run(ff.update_checkout(commit, lambda t, m: lines.append(m), checkout=checkout, remote_url=str(upstream)))
    return lines


def test_the_checkout_moves_forward_to_the_fix(repos):
    upstream, checkout = repos
    fix = commit_file(upstream, "VERSION", "0.1.1")
    run_update(fix, checkout, upstream)
    assert git(checkout, "rev-parse", "HEAD") == fix


def test_a_commit_not_on_the_branch_is_refused(repos):
    upstream, checkout = repos
    before = git(checkout, "rev-parse", "HEAD")
    with pytest.raises(ff.CheckoutError, match="is not on branch 'release-0.1'"):
        run_update("b" * 40, checkout, upstream)
    assert git(checkout, "rev-parse", "HEAD") == before


def test_a_checkout_that_cannot_fast_forward_is_left_as_it_is(repos):
    upstream, checkout = repos
    fix = commit_file(upstream, "VERSION", "0.1.1")
    local = commit_file(checkout, "LOCAL", "a local change")
    with pytest.raises(ff.CheckoutError, match="merge --ff-only"):
        run_update(fix, checkout, upstream)
    assert git(checkout, "rev-parse", "HEAD") == local


def test_a_checkout_on_another_branch_is_refused(repos):
    upstream, checkout = repos
    fix = commit_file(upstream, "VERSION", "0.1.1")
    git(checkout, "checkout", "-q", "-b", "work")
    with pytest.raises(ff.CheckoutError, match="is on branch 'work'"):
        run_update(fix, checkout, upstream)
