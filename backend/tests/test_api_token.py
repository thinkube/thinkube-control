#!/usr/bin/env python3
"""The API token the git hooks use stays equal to the platform's.

The shell cases run the generated scripts in bash, with curl, kubectl and git
replaced by stubs on PATH. The control API accepts one token, GOOD; the
kubectl stub hands out GOOD as the secret's value when it is present.
"""

import base64
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from api_token import TOKEN_HELP, needs_writing, write_token_file  # noqa: E402
from git_hooks import pre_commit_hook, regenerate_script  # noqa: E402

GOOD = "tk_good_token_from_the_secret"
STALE = "tk_stale_token_from_an_old_install"

CURL = r'''#!/bin/bash
token=""; prev=""
for a in "$@"; do
  if [ "$prev" = "-H" ]; then
    case "$a" in "Authorization: Bearer "*) token="${a#Authorization: Bearer }";; esac
  fi
  prev="$a"
done
printf '%s\n' "$token" >> "$CURL_CALLS"
if [ "$token" = "$GOOD" ]; then
  printf '{"files_generated":["workflows.yaml"]}\n200'
else
  printf '{"detail":"Not authenticated"}\n401'
fi
'''

KUBECTL = r'''#!/bin/bash
printf '%s\n' "$*" >> "$KUBECTL_CALLS"
printf '%s' "$GOOD" | base64
'''

GIT = r'''#!/bin/bash
case "$1 $2" in
  "diff --cached") printf '%s\n' "${STAGED:-}" ;;
  "add k8s/") printf 'staged\n' >> "$GIT_ADDS" ;;
esac
'''


# --- the file writer ---------------------------------------------------------


def test_an_absent_file_needs_writing(tmp_path):
    assert needs_writing(tmp_path / "api-token", GOOD)


def test_a_file_equal_to_the_secret_is_left_alone(tmp_path):
    f = tmp_path / "api-token"
    f.write_text(GOOD)
    assert not needs_writing(f, GOOD)


def test_a_file_that_differs_from_the_secret_is_rewritten(tmp_path):
    """The copy that outlived its installation is the case this exists for."""
    f = tmp_path / "api-token"
    f.write_text(STALE)
    assert needs_writing(f, GOOD)


def test_the_file_is_readable_by_its_owner_only(tmp_path):
    f = tmp_path / "nested" / "api-token"
    write_token_file(f, GOOD)
    assert f.read_text() == GOOD
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_rewriting_replaces_the_stale_value(tmp_path):
    f = tmp_path / "api-token"
    f.write_text(STALE)
    f.chmod(0o644)
    write_token_file(f, GOOD)
    assert f.read_text() == GOOD
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


# --- the generated scripts, run in bash --------------------------------------


class Box:
    """A home directory and a PATH with stubbed curl, git and, if wanted, kubectl."""

    def __init__(self, root: Path, kubectl: bool):
        self.home = root / "home"
        self.home.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "k8s").mkdir()
        self.curl_calls = root / "curl_calls"
        self.kubectl_calls = root / "kubectl_calls"
        self.git_adds = root / "git_adds"
        for name, body in (("curl", CURL), ("git", GIT)) + (
            (("kubectl", KUBECTL),) if kubectl else ()
        ):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)

    @property
    def token_file(self) -> Path:
        return self.home / ".thinkube" / "api-token"

    def with_token_file(self, value: str) -> "Box":
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(value)
        return self

    def run(self, script: str, extra_env=None):
        path = self.repo / "script.sh"
        path.write_text(script)
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "GOOD": GOOD,
            "CURL_CALLS": str(self.curl_calls),
            "KUBECTL_CALLS": str(self.kubectl_calls),
            "GIT_ADDS": str(self.git_adds),
        }
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", str(path)], cwd=self.repo, env=env,
            capture_output=True, text=True, timeout=30,
        )

    def tokens_sent(self):
        if not self.curl_calls.exists():
            return []
        return self.curl_calls.read_text().split()

    def kubectl_was_called(self):
        return self.kubectl_calls.exists()


REGEN = regenerate_script("wf-check", "thinkube.com", "https://control.thinkube.com")
HOOK = pre_commit_hook("wf-check", "thinkube.com", "https://control.thinkube.com")


@pytest.mark.parametrize("script", [REGEN, HOOK], ids=["regenerate", "hook"])
def test_the_generated_script_is_valid_bash(tmp_path, script):
    p = tmp_path / "s.sh"
    p.write_text(script)
    result = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_a_current_token_is_used_without_asking_the_cluster(tmp_path):
    box = Box(tmp_path, kubectl=True).with_token_file(GOOD)
    r = box.run(REGEN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.tokens_sent() == [GOOD]
    assert not box.kubectl_was_called()


def test_a_stale_token_is_refreshed_from_the_secret_and_the_call_retried(tmp_path):
    box = Box(tmp_path, kubectl=True).with_token_file(STALE)
    r = box.run(REGEN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.tokens_sent() == [STALE, GOOD], "rejected once, then accepted"
    assert box.token_file.read_text() == GOOD, "the copy now equals the secret"
    assert stat.S_IMODE(box.token_file.stat().st_mode) == 0o600


def test_without_kubectl_a_stale_token_fails_and_says_how_to_fix_it(tmp_path):
    box = Box(tmp_path, kubectl=False).with_token_file(STALE)
    r = box.run(REGEN)
    assert r.returncode == 1
    assert "HTTP 401" in r.stdout
    assert "mcp-default-token" in r.stdout
    assert box.tokens_sent() == [STALE], "no retry without a fresh token"
    assert box.token_file.read_text() == STALE, "nothing to replace it with"


def test_the_help_prints_the_command_exactly(tmp_path):
    """Quotes and braces must reach the terminal as the command needs them."""
    box = Box(tmp_path, kubectl=False).with_token_file(STALE)
    r = box.run(REGEN)
    for line in TOKEN_HELP.splitlines():
        assert line in r.stdout


def test_no_file_and_no_variable_takes_the_token_from_the_secret(tmp_path):
    box = Box(tmp_path, kubectl=True)
    r = box.run(REGEN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.tokens_sent() == [GOOD]
    assert box.token_file.read_text() == GOOD


def test_no_token_anywhere_stops_before_calling_the_api(tmp_path):
    box = Box(tmp_path, kubectl=False)
    r = box.run(REGEN)
    assert r.returncode == 1
    assert "No API token found" in r.stdout
    assert box.tokens_sent() == []


def test_the_variable_is_used_when_there_is_no_file(tmp_path):
    box = Box(tmp_path, kubectl=False)
    r = box.run(REGEN, {"THINKUBE_API_TOKEN": GOOD})
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.tokens_sent() == [GOOD]


# --- the pre-commit hook -----------------------------------------------------


def test_a_commit_that_does_not_touch_thinkube_yaml_does_nothing(tmp_path):
    """Most commits: no API call, and no kubectl either."""
    box = Box(tmp_path, kubectl=True).with_token_file(STALE)
    r = box.run(HOOK, {"STAGED": "backend/app.py"})
    assert r.returncode == 0
    assert box.tokens_sent() == []
    assert not box.kubectl_was_called()


def test_the_hook_recovers_a_stale_token_and_stages_the_manifests(tmp_path):
    box = Box(tmp_path, kubectl=True).with_token_file(STALE)
    r = box.run(HOOK, {"STAGED": "thinkube.yaml"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.tokens_sent() == [STALE, GOOD]
    assert box.git_adds.exists(), "k8s/ was staged"
    assert box.token_file.read_text() == GOOD


def test_the_hook_blocks_the_commit_when_it_cannot_recover(tmp_path):
    box = Box(tmp_path, kubectl=False).with_token_file(STALE)
    r = box.run(HOOK, {"STAGED": "thinkube.yaml"})
    assert r.returncode == 1
    assert "git commit --no-verify" in r.stdout
    assert not box.git_adds.exists()
