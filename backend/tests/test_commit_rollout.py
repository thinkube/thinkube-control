#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A pushed commit is traced to its push, its build, the build's image-tag commit and the images running now."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import commit_rollout as cr  # noqa: E402

APP = "research-debate"


def feed_entry(created, head, ref="refs/heads/main"):
    content = {"Commits": [{"Sha1": head}], "HeadCommit": {"Sha1": head}, "Len": 1}
    return {"id": 1, "op_type": "commit_repo", "created": created, "ref_name": ref, "content": json.dumps(content)}


def workflow(name, uid, webhook, phase="Succeeded"):
    return {
        "metadata": {"name": name, "uid": uid},
        "spec": {"arguments": {"parameters": [{"name": "repo-name", "value": APP},
                                              {"name": "webhook-timestamp", "value": webhook}]}},
        "status": {"phase": phase, "startedAt": webhook, "finishedAt": None},
    }


# main, newest first, as the Gitea commits API lists it
MAIN = [
    cr.MainCommit("u2" + "0" * 38, f"build: automatic update of {APP} to uid-b2"),
    cr.MainCommit("c2" + "0" * 38, "Ask every position for a stance"),
    cr.MainCommit("u1" + "0" * 38, f"build: automatic update of {APP} to uid-b1"),
    cr.MainCommit("c1" + "0" * 38, "Store each batch as it answers"),
]
POSITION = {c.sha: i for i, c in enumerate(MAIN)}
PUSHES = cr.parse_feed([
    feed_entry("2026-09-18T09:00:54Z", MAIN[0].sha),
    feed_entry("2026-09-18T08:54:36Z", MAIN[1].sha),
    feed_entry("2026-09-18T08:29:15Z", MAIN[2].sha),
    feed_entry("2026-09-18T08:23:23Z", MAIN[3].sha),
])
BUILDS = [
    cr.parse_build(workflow(f"{APP}-build-b1", "uid-b1", "2026-09-18T08:23:24Z")),
    cr.parse_build(workflow(f"{APP}-build-b2", "uid-b2", "2026-09-18T08:54:37Z")),
]


def test_the_push_that_delivered_a_commit_is_the_first_whose_head_reaches_it():
    push = cr.delivering_push(PUSHES, POSITION, cr.find_commit(MAIN, "c1"))
    assert push.head == MAIN[3].sha


def test_a_commit_pushed_together_with_a_later_one_is_delivered_by_that_push():
    pushes = [p for p in PUSHES if p.head != MAIN[3].sha]
    assert cr.delivering_push(pushes, POSITION, 3).head == MAIN[2].sha


def test_the_build_of_a_push_is_the_first_one_after_it():
    build, note = cr.build_of_push(PUSHES[2], PUSHES, BUILDS)
    assert build.name == f"{APP}-build-b2" and "1s after the push" in note


def test_a_push_without_a_build_after_it_has_none():
    build, note = cr.build_of_push(PUSHES[-1], PUSHES, BUILDS)
    assert build is None and note == "no build started after this push"


def test_a_build_after_another_push_cannot_be_told_apart():
    build, note = cr.build_of_push(PUSHES[1], PUSHES, BUILDS)
    assert build is None and "cannot be told which push it built" in note


def test_the_automatic_update_commit_is_found_by_the_build_tag():
    assert cr.automatic_update(MAIN, APP, "uid-b2").sha == MAIN[0].sha
    assert cr.automatic_update(MAIN, APP, "uid-b3") is None


def test_the_running_image_is_placed_relative_to_the_commit():
    b1, b2 = BUILDS
    at_c1 = cr.find_commit(MAIN, "c1")
    at_c2 = cr.find_commit(MAIN, "c2")
    assert cr.image_source("uid-b1", b1, BUILDS, PUSHES, MAIN, POSITION, at_c1)["source"] == "this_commit"
    later = cr.image_source("uid-b2", b1, BUILDS, PUSHES, MAIN, POSITION, at_c1)
    assert later["source"] == "later_commit" and later["commit"] == MAIN[1].sha
    assert cr.image_source("uid-b1", b2, BUILDS, PUSHES, MAIN, POSITION, at_c2)["source"] == "earlier_commit"
    assert cr.image_source("uid-gone", b2, BUILDS, PUSHES, MAIN, POSITION, at_c2)["source"] == "unknown"


def test_live_needs_every_deployment_on_this_or_a_later_build_and_rolled_out():
    done = {"deployment": "backend", "source": "later_commit", "rollout_complete": True}
    assert cr.verdict([done])[0] is True
    assert cr.verdict([done, {**done, "deployment": "frontend", "source": "earlier_commit"}])[0] is False
    assert cr.verdict([{**done, "rollout_complete": False}]) == (
        False, "built, but the rollout is not complete in backend")
    assert cr.verdict([])[0] is False


def test_a_commit_not_on_main_is_refused_by_name():
    with pytest.raises(LookupError, match="deadbeef is not on main"):
        cr.find_commit(MAIN, "deadbeef")


def test_the_image_tag_is_read_from_the_reference():
    assert cr.image_tag("registry.thinkube.com/thinkube/research-debate-backend:uid-b2") == "uid-b2"
    with pytest.raises(ValueError, match="has no tag"):
        cr.image_tag("registry.thinkube.com/thinkube/research-debate-backend")
