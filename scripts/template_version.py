# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
The version of a template: the release tag a deploy generates from.

A template release is a git tag vMAJOR.MINOR.PATCH on the template
repository. A deploy generates the application from one release, so the
version is the exact code the application came from. Other tags are ignored.

A template in the thinkube GitHub organization is part of the platform: its
release is the newest tag whose MAJOR.MINOR equals the platform's version,
read from THINKUBE_PLATFORM_VERSION. Any other template (one a user published
from an app) carries its own version numbers: its release is its newest tag.

A cluster follows one branch of the platform, THINKUBE_BRANCH. A cluster
that follows a release branch (release-MAJOR.MINOR) is a user's cluster and
deploys platform templates from their release tags. A cluster that follows
any other branch develops the platform: it deploys a platform template from
the commit at the head of that branch, so a template change is seen running
there before it is tagged, and a tag is what every other cluster receives.
"""

import os
import re
import subprocess
from typing import Dict

RELEASE_TAG = re.compile(r"^refs/tags/v(\d+)\.(\d+)\.(\d+)$")
PLATFORM_VERSION = re.compile(r"^(\d+)\.(\d+)$")
PLATFORM_TEMPLATE = re.compile(r"^https://github\.com/thinkube/", re.IGNORECASE)
RELEASE_BRANCH = re.compile(r"^release-\d+\.\d+$")


class TemplateNotReleased(RuntimeError):
    """The template repository has no release tag a deploy can use."""


def platform_version() -> tuple:
    """The platform's MAJOR.MINOR, from THINKUBE_PLATFORM_VERSION, as (major, minor)."""
    value = os.environ.get("THINKUBE_PLATFORM_VERSION")
    if not value:
        raise RuntimeError(
            "THINKUBE_PLATFORM_VERSION is not set; thinkube-control receives it from the "
            "platform_version answer of its deploy"
        )
    match = PLATFORM_VERSION.match(value)
    if not match:
        raise RuntimeError(f"THINKUBE_PLATFORM_VERSION is '{value}', expected MAJOR.MINOR, for example 0.1")
    return tuple(int(part) for part in match.groups())


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
    """The release tag a deploy of the template generates from.

    For a template in the thinkube organization, the newest vMAJOR.MINOR.PATCH
    tag whose MAJOR.MINOR is the platform's version; for any other template,
    its newest vMAJOR.MINOR.PATCH tag.

    Raises TemplateNotReleased when the repository has no such tag, and
    RuntimeError when its tags cannot be read or THINKUBE_PLATFORM_VERSION is
    missing for a platform template.
    """
    wanted = platform_version() if PLATFORM_TEMPLATE.match(template_url) else None

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
            version = tuple(int(part) for part in match.groups())
            if wanted is None or version[:2] == wanted:
                releases.append((version, ref[len("refs/tags/"):]))
    if not releases:
        if wanted is not None:
            raise TemplateNotReleased(
                f"{template_url} has no release tag for platform version {wanted[0]}.{wanted[1]} "
                f"(v{wanted[0]}.{wanted[1]}.PATCH). Tag a release of the template for this "
                f"platform version, for example: git tag v{wanted[0]}.{wanted[1]}.0 && "
                f"git push origin v{wanted[0]}.{wanted[1]}.0"
            )
        raise TemplateNotReleased(
            f"{template_url} has no release tag (vMAJOR.MINOR.PATCH). "
            "Tag the template's current state to release it, for example: "
            "git tag v0.1.0 && git push origin v0.1.0"
        )
    return max(releases)[1]


def platform_branch() -> str:
    """The branch of the platform this cluster follows, from THINKUBE_BRANCH."""
    branch = os.environ.get("THINKUBE_BRANCH")
    if not branch:
        raise RuntimeError(
            "THINKUBE_BRANCH is not set; thinkube-control receives it from the "
            "thinkube_branch answer of its deploy"
        )
    return branch


def branch_head(template_url: str, branch: str) -> str:
    """The commit at the head of a branch of the template repository.

    Raises TemplateNotReleased when the repository has no such branch, and
    RuntimeError when its branches cannot be read.
    """
    result = subprocess.run(
        ["git", "ls-remote", "--heads", template_url, f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        env=github_git_env(),
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Cannot read the branches of {template_url}: {result.stderr.strip()}")
    for line in result.stdout.splitlines():
        sha, ref = line.split("\t")
        if ref == f"refs/heads/{branch}":
            return sha
    raise TemplateNotReleased(
        f"{template_url} has no branch '{branch}', the branch this cluster follows "
        f"(THINKUBE_BRANCH). Create it, or deploy on a cluster that follows a release branch."
    )


def template_ref(template_url: str) -> str:
    """The git ref a deploy of the template generates from.

    A platform template on a cluster that follows a branch other than a
    release branch: the commit at the head of that branch. Every other case:
    the release tag, as latest_release_tag gives it.
    """
    if PLATFORM_TEMPLATE.match(template_url):
        branch = platform_branch()
        if not RELEASE_BRANCH.match(branch):
            return branch_head(template_url, branch)
    return latest_release_tag(template_url)
