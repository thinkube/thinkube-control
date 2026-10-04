# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
News and fixes for the installed platform, read from thinkube/thinkube-fixes.

The feed is one JSON file per platform release, releases/<MAJOR.MINOR>.json on
the main branch of the public thinkube-fixes repository. Thinkube Control keeps
a clone of it in the platform tree, thinkube-platform/fixes/thinkube-fixes,
fetches main into it, and reads the file from the fetched commit, so what it
read is a commit anyone can look at in Thinkube IDE. The working tree is not
touched. The feed holds news entries
and fix entries. A fix entry is a pointer, not code: it names a component, the
component VERSION that carries the fix, the commit on the platform's release
branch of thinkube/thinkube, and the playbook to run. Applying a fix moves the
cluster's thinkube checkout forward to the release branch and runs that
playbook, so only code from thinkube/thinkube ever runs.

A fix is applied when the version the component's discovery ConfigMap reports
is at least the fix's version: the playbook writes the new VERSION there, so no
other record of applied fixes is kept.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.fixes import FixFeed, NewsRead
from app.services.metadata_fetcher import platform_ref

logger = logging.getLogger(__name__)

# Public, so the fetch needs no credentials in the thinkube-control pod.
FEED_REPO_URL = "https://github.com/thinkube/thinkube-fixes.git"
FEED_BRANCH = "main"
# The clone, in the platform tree shared with Thinkube IDE (category fixes).
FEED_CHECKOUT = Path("/home/thinkube/thinkube-platform/fixes/thinkube-fixes")
CHECK_INTERVAL_SECONDS = 6 * 3600

# The periodic check and "Check now" share the clone's FETCH_HEAD.
_fetch_lock = threading.Lock()

# The thinkube checkout the playbooks run from, shared with Thinkube IDE.
THINKUBE_CHECKOUT = Path("/home/thinkube/thinkube-platform/core/thinkube")
# Public, so the fetch needs no credentials in the thinkube-control pod.
THINKUBE_PUBLIC_URL = "https://github.com/thinkube/thinkube.git"

# A playbook that redeploys thinkube-control restarts the pod running it, so a
# fix of thinkube-control is applied from Thinkube IDE.
SELF_COMPONENT = "thinkube-control"

APPLIED = "applied"
AVAILABLE = "available"
NOT_INSTALLED = "not_installed"
APPLY_FROM_IDE = "apply_from_ide"

SEVERITIES = ("security", "bug")
KINDS = ("core", "optional")

_PLATFORM_VERSION = re.compile(r"^\d+\.\d+$")
_COMPONENT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PLAYBOOK = re.compile(r"^ansible/40_thinkube/(core|optional)/[A-Za-z0-9_./-]+\.ya?ml$")

NEWS_FIELDS = ("id", "date", "title", "body")
FIX_FIELDS = ("id", "date", "title", "body", "severity", "component", "kind", "fixed_in", "commit", "playbook")


class FeedError(Exception):
    """The feed could not be read, or it does not follow the feed format."""


class CheckoutError(Exception):
    """The thinkube checkout could not be moved to the fix's commit."""


def platform_version() -> str:
    """The platform's MAJOR.MINOR, from THINKUBE_PLATFORM_VERSION."""
    value = os.environ.get("THINKUBE_PLATFORM_VERSION")
    if not value:
        raise FeedError(
            "THINKUBE_PLATFORM_VERSION is not set; thinkube-control receives it from the "
            "platform_version answer of its deploy"
        )
    if not _PLATFORM_VERSION.match(value):
        raise FeedError(f"THINKUBE_PLATFORM_VERSION is '{value}', expected MAJOR.MINOR, for example 0.1")
    return value


def feed_path() -> str:
    """The feed file of this platform release, inside the repository."""
    return f"releases/{platform_version()}.json"


def version_tuple(value: str) -> tuple:
    return tuple(int(part) for part in value.split("."))


def _check_entry(entry: Any, fields: tuple, where: str) -> None:
    if not isinstance(entry, dict):
        raise FeedError(f"{where} is not an object")
    missing = [f for f in fields if f not in entry]
    if missing:
        raise FeedError(f"{where} has no {', '.join(missing)}")
    extra = [f for f in entry if f not in fields]
    if extra:
        raise FeedError(f"{where} has unknown fields: {', '.join(extra)}")
    for f in fields:
        if not isinstance(entry[f], str) or not entry[f].strip():
            raise FeedError(f"{where}: {f} must be a non-empty string")
    if not _ID.match(entry["id"]):
        raise FeedError(f"{where}: id '{entry['id']}' must be lower case letters, digits and dashes")
    if not _DATE.match(entry["date"]):
        raise FeedError(f"{where}: date '{entry['date']}' must be YYYY-MM-DD")


def validate_feed(data: Any) -> Dict[str, List[Dict[str, str]]]:
    """The feed's news and fixes; FeedError names the first entry that is wrong.

    A feed with one wrong entry is refused whole, so a cluster never acts on
    part of a feed.
    """
    if not isinstance(data, dict):
        raise FeedError("The feed is not a JSON object")
    unknown = [k for k in data if k not in ("news", "fixes")]
    if unknown:
        raise FeedError(f"The feed has unknown keys: {', '.join(unknown)}")
    for key in ("news", "fixes"):
        if not isinstance(data.get(key), list):
            raise FeedError(f"The feed has no '{key}' list")

    seen = set()
    for i, entry in enumerate(data["news"]):
        _check_entry(entry, NEWS_FIELDS, f"news[{i}]")
        if entry["id"] in seen:
            raise FeedError(f"news[{i}]: id '{entry['id']}' is used twice")
        seen.add(entry["id"])

    for i, entry in enumerate(data["fixes"]):
        where = f"fixes[{i}]"
        _check_entry(entry, FIX_FIELDS, where)
        if entry["id"] in seen:
            raise FeedError(f"{where}: id '{entry['id']}' is used twice")
        seen.add(entry["id"])
        if entry["severity"] not in SEVERITIES:
            raise FeedError(f"{where}: severity must be one of {', '.join(SEVERITIES)}")
        if entry["kind"] not in KINDS:
            raise FeedError(f"{where}: kind must be one of {', '.join(KINDS)}")
        if not _COMPONENT.match(entry["component"]):
            raise FeedError(f"{where}: component '{entry['component']}' is not a component name")
        if not _COMPONENT_VERSION.match(entry["fixed_in"]):
            raise FeedError(f"{where}: fixed_in '{entry['fixed_in']}' must be MAJOR.MINOR.PATCH")
        if not _COMMIT.match(entry["commit"]):
            raise FeedError(f"{where}: commit must be a full 40-character commit id")
        if not _PLAYBOOK.match(entry["playbook"]) or ".." in entry["playbook"]:
            raise FeedError(f"{where}: playbook '{entry['playbook']}' is not a component playbook path")
        if not entry["playbook"].startswith(f"ansible/40_thinkube/{entry['kind']}/"):
            raise FeedError(f"{where}: playbook is not under ansible/40_thinkube/{entry['kind']}/")

    return {"news": data["news"], "fixes": data["fixes"]}


def _feed_git(*args: str, cwd: Optional[Path] = None) -> str:
    """Run git for the feed clone; FeedError carries git's own message."""
    cmd = ["git", *args] if cwd is None else ["git", "-C", str(cwd), *args]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise FeedError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def fetch_feed(
    path: str,
    checkout: Path = FEED_CHECKOUT,
    repo_url: str = FEED_REPO_URL,
) -> tuple:
    """Fetch the feed repository's main branch and return (feed, commit).

    The clone is made on the first check. The file is read from the fetched
    commit and validated whole.
    """
    with _fetch_lock:
        if not (checkout / ".git").exists():
            checkout.parent.mkdir(parents=True, exist_ok=True)
            _feed_git("clone", "--quiet", "--branch", FEED_BRANCH, repo_url, str(checkout))
        _feed_git("fetch", "--quiet", repo_url, f"refs/heads/{FEED_BRANCH}", cwd=checkout)
        commit = _feed_git("rev-parse", "FETCH_HEAD", cwd=checkout).strip()
        try:
            text = _feed_git("show", f"FETCH_HEAD:{path}", cwd=checkout)
        except FeedError as e:
            raise FeedError(f"{path} does not exist at {repo_url} {FEED_BRANCH} ({commit[:12]}): {e}") from e
    try:
        data = json.loads(text)
    except ValueError as e:
        raise FeedError(f"{path} at {commit[:12]} is not valid JSON: {e}") from e
    return validate_feed(data), commit


def _feed_row(db: Session) -> FixFeed:
    row = db.query(FixFeed).filter(FixFeed.id == 1).first()
    if row is None:
        row = FixFeed(id=1)
        db.add(row)
    return row


def check_now(db: Session) -> FixFeed:
    """Read the feed and record the result.

    A failed check records its error and keeps the last valid feed, so what
    the page shows stays what was last read correctly, with the error beside it.
    """
    row = _feed_row(db)
    now = datetime.now(timezone.utc)
    row.url = FEED_REPO_URL
    try:
        feed, commit = fetch_feed(feed_path())
    except FeedError as e:
        row.last_checked = now
        row.last_error = str(e)
        db.commit()
        logger.warning(f"News and fixes check failed: {e}")
        return row
    row.last_checked = now
    row.last_error = None
    row.payload = feed
    row.payload_fetched = now
    row.feed_commit = commit
    db.commit()
    logger.info(f"News and fixes check: {len(feed['news'])} news, {len(feed['fixes'])} fixes at {commit[:12]}")
    return row


def installed_versions(list_configmaps: Callable[..., Any]) -> Dict[str, str]:
    """Component name -> installed version, from the discovery ConfigMaps' labels.

    list_configmaps is CoreV1Api.list_config_map_for_all_namespaces.
    """
    result = list_configmaps(label_selector="thinkube.io/managed=true")
    versions = {}
    for cm in result.items:
        labels = cm.metadata.labels or {}
        name = labels.get("thinkube.io/service-name")
        version = labels.get("thinkube.io/component-version")
        if name and version and _COMPONENT_VERSION.match(version):
            versions[name] = version
    return versions


def fix_status(fix: Dict[str, str], installed: Dict[str, str]) -> str:
    version = installed.get(fix["component"])
    if version is None:
        return NOT_INSTALLED
    if version_tuple(version) >= version_tuple(fix["fixed_in"]):
        return APPLIED
    if fix["component"] == SELF_COMPONENT:
        return APPLY_FROM_IDE
    return AVAILABLE


def ide_command(fix: Dict[str, str]) -> str:
    """The command that applies a fix from Thinkube IDE."""
    return (
        f"cd {THINKUBE_CHECKOUT} && git pull --ff-only && "
        f"./scripts/tk_ansible {fix['playbook']}"
    )


def feed_view(db: Session, installed: Dict[str, str]) -> Dict[str, Any]:
    """The feed with each fix's status and each news entry's read state."""
    row = db.query(FixFeed).filter(FixFeed.id == 1).first()
    payload = (row.payload if row else None) or {"news": [], "fixes": []}
    read = {r.news_id for r in db.query(NewsRead).all()}

    fixes = []
    for fix in payload["fixes"]:
        status = fix_status(fix, installed)
        fixes.append({
            **fix,
            "status": status,
            "installed_version": installed.get(fix["component"]),
            "ide_command": ide_command(fix) if status == APPLY_FROM_IDE else None,
        })
    # Security fixes first, the newest first within each severity.
    fixes.sort(key=lambda f: f["date"], reverse=True)
    fixes.sort(key=lambda f: SEVERITIES.index(f["severity"]))

    news = sorted(
        ({**n, "read": n["id"] in read} for n in payload["news"]),
        key=lambda n: n["date"],
        reverse=True,
    )
    pending = sum(1 for f in fixes if f["status"] in (AVAILABLE, APPLY_FROM_IDE))
    unread = sum(1 for n in news if not n["read"])
    return {
        "url": row.url if row else None,
        "last_checked": row.last_checked.isoformat() if row and row.last_checked else None,
        "last_error": row.last_error if row else None,
        "feed_fetched": row.payload_fetched.isoformat() if row and row.payload_fetched else None,
        "feed_commit": row.feed_commit if row else None,
        "news": news,
        "fixes": fixes,
        "pending_fixes": pending,
        "unread_news": unread,
    }


def find_fix(db: Session, fix_id: str) -> Optional[Dict[str, str]]:
    row = db.query(FixFeed).filter(FixFeed.id == 1).first()
    if row is None or not row.payload:
        return None
    return next((f for f in row.payload["fixes"] if f["id"] == fix_id), None)


async def _git(args: List[str], log: Callable[[str, str], None], checkout: Path) -> str:
    """Run git in the checkout, log its output, and return it; CheckoutError on failure."""
    log("output", f"$ git {' '.join(args)}")
    process = await asyncio.create_subprocess_exec(
        "git", "-C", str(checkout), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    out, _ = await process.communicate()
    text = out.decode("utf-8", errors="replace").strip()
    for line in text.splitlines():
        log("output", line)
    if process.returncode != 0:
        raise CheckoutError(f"git {' '.join(args)} failed: {text or f'exit code {process.returncode}'}")
    return text


async def update_checkout(
    commit: str,
    log: Callable[[str, str], None],
    checkout: Path = THINKUBE_CHECKOUT,
    remote_url: str = THINKUBE_PUBLIC_URL,
) -> None:
    """Move the thinkube checkout forward to the release branch, which must hold commit.

    The checkout only moves forward: when it is on another branch, or git
    cannot fast-forward it, CheckoutError carries git's own message and the
    checkout is left as it was.
    """
    branch = platform_ref()
    current = await _git(["symbolic-ref", "--short", "HEAD"], log, checkout)
    if current != branch:
        raise CheckoutError(
            f"The thinkube checkout {checkout} is on branch '{current}'; fixes follow '{branch}'. "
            f"Switch it back with: git -C {checkout} checkout {branch}"
        )
    await _git(["fetch", remote_url, f"refs/heads/{branch}"], log, checkout)
    try:
        await _git(["merge-base", "--is-ancestor", commit, "FETCH_HEAD"], log, checkout)
    except CheckoutError as e:
        raise CheckoutError(
            f"Commit {commit} is not on branch '{branch}' of {remote_url}: {e}"
        ) from e
    await _git(["merge", "--ff-only", "FETCH_HEAD"], log, checkout)
    log("output", f"The thinkube checkout is at {branch}, which holds {commit[:12]}")
