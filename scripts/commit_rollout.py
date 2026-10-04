# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Whether a commit pushed to an app's Gitea repository is live, from what Gitea and the cluster record.

A push to main of thinkube-deployments/<app> makes Gitea send a webhook; the
sensor gitea-push creates one build workflow <app>-build-* labelled
thinkube.io/app-name=<app>, whose webhook-timestamp parameter is the time the
event arrived, seconds after the push. The build tags its images with the
workflow's uid. When the image is in Harbor, the webhook adapter commits
"build: automatic update of <app> to <uid>" to main, and ArgoCD rolls the
app's deployments to that image.

Neither the push nor the build records the other, so they are matched by
time: Gitea's activity feed records each push to main with its time and head
commit. The push that delivered a commit is the first push whose head is the
commit or a later one on main. Its build is the first build whose webhook
arrived at or after the push, provided no other push came in between; when
one did, the builds cannot be told apart and the answer says so.

An image comes from a build; the build from a push; the push has a head
commit. The running image comes from the given commit when its build is the
commit's build, from a later commit when its push's head is later on main, and
from an earlier one when that head is older: the commit is then not live yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

MAIN = "refs/heads/main"


@dataclass(frozen=True)
class MainCommit:
    sha: str
    subject: str


@dataclass(frozen=True)
class Push:
    time: datetime
    head: str
    ref: str


@dataclass(frozen=True)
class Build:
    name: str
    uid: str
    phase: Optional[str]
    webhook_time: Optional[datetime]
    started_at: Optional[str]
    finished_at: Optional[str]


def parse_time(value: str) -> datetime:
    """A Gitea or Kubernetes timestamp, such as 2026-09-18T08:54:36Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_feed(entries: list[dict]) -> list[Push]:
    """The pushes in Gitea's activity feed of a repository, oldest first."""
    pushes = []
    for entry in entries:
        if entry.get("op_type") != "commit_repo":
            continue
        content = json.loads(entry["content"])
        head = (content.get("HeadCommit") or {}).get("Sha1")
        if not head:
            raise ValueError(f"Gitea feed entry {entry.get('id')} records a push without a head commit")
        pushes.append(Push(parse_time(entry["created"]), head, entry["ref_name"]))
    return sorted(pushes, key=lambda p: p.time)


def parse_build(workflow: dict) -> Build:
    """A build workflow as the Argo API returns it."""
    params = {p["name"]: p.get("value") for p in workflow.get("spec", {}).get("arguments", {}).get("parameters", [])}
    status = workflow.get("status", {})
    webhook = params.get("webhook-timestamp")
    return Build(
        name=workflow["metadata"]["name"],
        uid=workflow["metadata"]["uid"],
        phase=status.get("phase"),
        webhook_time=parse_time(webhook) if webhook else None,
        started_at=status.get("startedAt"),
        finished_at=status.get("finishedAt"),
    )


def find_commit(main: list[MainCommit], commit: str) -> int:
    """The position of a commit, given by its full or abbreviated sha, on main (0 is the newest)."""
    found = [i for i, c in enumerate(main) if c.sha.startswith(commit.lower())]
    if not found:
        raise LookupError(f"commit {commit} is not on main (read the {len(main)} newest commits)")
    if len(found) > 1:
        raise LookupError(f"commit {commit} is ambiguous on main: {', '.join(main[i].sha[:12] for i in found)}")
    return found[0]


def delivering_push(pushes: list[Push], position: dict[str, int], at: int) -> Push:
    """The first push to main whose head is the commit at `at` or a later one."""
    for push in pushes:
        if push.ref == MAIN and push.head in position and position[push.head] <= at:
            return push
    raise LookupError("Gitea's activity feed records no push to main that delivered this commit")


def build_of_push(push: Push, pushes: list[Push], builds: list[Build]) -> tuple[Optional[Build], str]:
    """The build a push triggered, and what is known about the match."""
    after = sorted(
        (b for b in builds if b.webhook_time is not None and b.webhook_time >= push.time),
        key=lambda b: b.webhook_time,
    )
    if not after:
        return None, "no build started after this push"
    build = after[0]
    between = [p for p in pushes if push.time < p.time <= build.webhook_time]
    if between:
        return None, (
            f"the first build after this push, {build.name}, started after another push "
            f"({between[0].head[:10]} to {between[0].ref}), so it cannot be told which push it built"
        )
    return build, f"{build.name} started {int((build.webhook_time - push.time).total_seconds())}s after the push"


def push_of_build(build: Build, pushes: list[Push]) -> Optional[Push]:
    """The push that triggered a build: the last push at or before its webhook."""
    if build.webhook_time is None:
        return None
    before = [p for p in pushes if p.time <= build.webhook_time]
    return before[-1] if before else None


def automatic_update(main: list[MainCommit], app_name: str, tag: str) -> Optional[MainCommit]:
    """The commit on main that set the app's images to a build's tag, when there is one."""
    subject = f"build: automatic update of {app_name} to {tag}"
    return next((c for c in main if c.subject == subject), None)


def image_tag(image: str) -> str:
    """The tag of an image reference registry/project/name:tag."""
    name = image.rsplit("/", 1)[-1]
    if ":" not in name:
        raise ValueError(f"image {image} has no tag")
    return name.split(":", 1)[1]


def image_source(
    tag: str,
    commit_build: Optional[Build],
    builds: list[Build],
    pushes: list[Push],
    main: list[MainCommit],
    position: dict[str, int],
    at: int,
) -> dict:
    """Where a running image comes from, relative to the commit at `at` on main."""
    if commit_build is not None and tag == commit_build.uid:
        return {"source": "this_commit", "note": f"built by {commit_build.name}, the build of this commit's push"}
    build = next((b for b in builds if b.uid == tag), None)
    if build is None:
        return {"source": "unknown", "note": f"no build workflow with uid {tag} is recorded in Argo Workflows"}
    push = push_of_build(build, pushes)
    if push is None or push.ref != MAIN or push.head not in position:
        return {"source": "unknown", "note": f"the push that started {build.name} is not on main"}
    head = main[position[push.head]]
    built = {"build": build.name, "commit": head.sha, "subject": head.subject}
    if position[push.head] < at:
        return {"source": "later_commit", **built, "note": "built from a later commit on main, which includes this one"}
    if position[push.head] > at:
        return {"source": "earlier_commit", **built, "note": "built from an earlier commit: this commit is not live"}
    return {"source": "this_commit", **built, "note": "built from a push whose head is this commit"}


def rollout_complete(deployment: dict) -> bool:
    """Whether every replica of a deployment runs its current template and is available."""
    wanted = deployment["replicas"]
    return deployment["updated_replicas"] == wanted and deployment["available_replicas"] == wanted


def verdict(running: list[dict]) -> tuple[bool, str]:
    """Whether the commit is live in every deployment of the app, and why in one sentence."""
    if not running:
        return False, "the app has no deployment running an image of its own"
    behind = [r for r in running if r["source"] not in ("this_commit", "later_commit")]
    if behind:
        names = ", ".join(f"{r['deployment']} ({r['source']})" for r in behind)
        return False, f"not live: {names} does not run an image built from this commit or a later one"
    rolling = [r["deployment"] for r in running if not r["rollout_complete"]]
    if rolling:
        return False, f"built, but the rollout is not complete in {', '.join(rolling)}"
    return True, "live: every deployment runs an image built from this commit or a later one"
