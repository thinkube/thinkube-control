# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
The version of a template: its newest release tag.

A template release is a git tag vMAJOR.MINOR.PATCH on the template
repository. A deploy generates the application from the newest one, so the
version is the exact code the application came from. Other tags are ignored.
"""

import os
import re
import subprocess
from typing import Dict

RELEASE_TAG = re.compile(r"^refs/tags/v(\d+)\.(\d+)\.(\d+)$")


class TemplateNotReleased(RuntimeError):
    """The template repository has no vMAJOR.MINOR.PATCH tag."""


def github_git_env() -> Dict[str, str]:
    """The environment for git commands that read GitHub template repositories.

    Templates published from an app are private repositories, so git
    authenticates with the platform's GitHub token. The token is handed to git
    for the process only, as a URL rewrite in the environment: nothing is
    written to a git config file, and the template address stays without it.
    """
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise RuntimeError("GITHUB_TOKEN is not set; thinkube-control receives it from the github-token secret")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = f"url.https://x-access-token:{github_token}@github.com/.insteadOf"
    env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    return env


def latest_release_tag(template_url: str) -> str:
    """The newest vMAJOR.MINOR.PATCH tag of the template repository.

    Raises TemplateNotReleased when the repository has none, and RuntimeError
    when its tags cannot be read.
    """
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", template_url],
        capture_output=True,
        text=True,
        env=github_git_env(),
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Cannot read the tags of {template_url}: {result.stderr.strip()}")

    releases = []
    for line in result.stdout.splitlines():
        ref = line.split("\t")[-1]
        match = RELEASE_TAG.match(ref)
        if match:
            releases.append((tuple(int(part) for part in match.groups()), ref[len("refs/tags/"):]))
    if not releases:
        raise TemplateNotReleased(
            f"{template_url} has no release tag (vMAJOR.MINOR.PATCH). "
            "Tag the template's current state to release it, for example: "
            "git tag v0.1.0 && git push origin v0.1.0"
        )
    return max(releases)[1]
