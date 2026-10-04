#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A deploy never resets a checkout that holds work Gitea does not have, and says what it replaces."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from checkout_state import (  # noqa: E402
    STATUS_COMMAND,
    UNPUSHED_COMMAND,
    changed_files,
    refusal,
    replacement_warning,
)
from component_checkout import Commit, parse_log  # noqa: E402


def test_a_clean_and_pushed_checkout_is_not_refused():
    assert refusal("notes", "/h/apps/notes", [], []) is None


def test_uncommitted_files_are_named_and_the_way_out_is_given():
    message = refusal("notes", "/h/apps/notes", ["backend/app/main.py", "thinkube.yaml"], [])
    assert "/h/apps/notes" in message and "git reset --hard" in message
    assert "2 changed file(s) not committed" in message
    assert "  backend/app/main.py" in message and "  thinkube.yaml" in message
    assert "Commit and push them first" in message


def test_unpushed_commits_are_named():
    message = refusal("notes", "/h/apps/notes", [], [Commit("a" * 40, "tkadmin", "Store each batch")])
    assert "1 commit(s) not pushed to Gitea" in message
    assert "aaaaaaaaaa tkadmin: Store each batch" in message


def test_porcelain_lines_become_paths():
    assert changed_files(" M backend/app/main.py\nM  README.md\nR  old.py -> new.py\n") == [
        "backend/app/main.py", "README.md", "old.py -> new.py",
    ]
    assert changed_files("") == []


def test_the_warning_says_what_is_replaced_and_is_absent_without_a_checkout():
    warning = replacement_warning("/h/apps/notes", checkout_exists=True)
    assert "resets the checkout /h/apps/notes to Gitea's main" in warning
    assert "copier copy --force" in warning and "regenerates k8s/" in warning
    assert "overwritten" not in warning
    assert replacement_warning("/h/apps/notes", checkout_exists=False) is None


def test_the_checkout_is_read_from_real_git(tmp_path):
    remote, checkout = tmp_path / "remote.git", tmp_path / "notes"

    def git(*args, cwd=checkout):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout

    def state():
        status = subprocess.run(STATUS_COMMAND, shell=True, cwd=checkout, capture_output=True, text=True, check=True)
        log = subprocess.run(UNPUSHED_COMMAND, shell=True, cwd=checkout, capture_output=True, text=True, check=True)
        return changed_files(status.stdout), parse_log(log.stdout)

    git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    git("clone", "-q", str(remote), str(checkout), cwd=tmp_path)
    git("config", "user.name", "tkadmin")
    git("config", "user.email", "tkadmin@thinkube.com")
    (checkout / "main.py").write_text("one\n")
    git("add", "main.py")
    git("commit", "-q", "-m", "First")
    git("push", "-q", "origin", "main")
    assert state() == ([], [])

    (checkout / "untracked.txt").write_text("scratch\n")
    assert state() == ([], [])

    (checkout / "main.py").write_text("two\n")
    assert state()[0] == ["main.py"]

    git("commit", "-q", "-am", "Second")
    changed, unpushed = state()
    assert changed == [] and [c.subject for c in unpushed] == ["Second"]
