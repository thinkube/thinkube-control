# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
A deploy from a folder of a repository.

A template is a whole repository, rendered by Copier. A folder deploy takes
one folder of a repository instead (template_path of the deploy request) and
copies it as it is: the folder holds the application's source, with no
template variables. The extensions of Thinkube Control are deployed this way,
from extensions/<name> of Control's own repository.

The application is named after the folder: other parts of the platform reach
it by that name (its Service, its address, its MCP server).

The copy replaces the checkout's files: a file removed from the folder is
removed from the application. The files the build writes are kept.
"""

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")

# Files of the checkout the build writes, which no deploy replaces: the image
# tags the cluster runs.
KEPT = re.compile(r"^k8s/\.argocd-source-[^/]+$")


def folder_segments(template_path: str) -> List[str]:
    """The folder's path as its segments; ValueError when it is not a plain relative path."""
    segments = template_path.split("/")
    if not template_path or any(not SEGMENT.match(s) for s in segments):
        raise ValueError(
            f"template_path '{template_path}' is not a folder of the repository: write it relative "
            f"to the repository's root, as folder/sub-folder, with no '..' and no leading or trailing '/'"
        )
    return segments


def folder_name(template_path: str) -> str:
    """The name an application deployed from this folder has: the folder's own name."""
    return folder_segments(template_path)[-1]


def source_refusal(
    name: str, deployed_url: str, deployed_path: Optional[str], url: str, path: Optional[str]
) -> Optional[str]:
    """Why a deploy of an application from (url, path) is refused, given where its last deploy came from.

    An application deployed from a folder holds nothing but that folder's
    files. A deploy from another source would write another application over
    it, so it is refused; any other change of source is allowed.
    """
    if not deployed_path:
        return None
    if path == deployed_path and url.rstrip("/") == deployed_url.rstrip("/"):
        return None
    return (
        f"{name} is deployed from the folder {deployed_path} of {deployed_url.rstrip('/')}. "
        f"Deploy it from there: template_url {deployed_url.rstrip('/')}, template_path {deployed_path}. "
        f"To deploy another source under this name, delete the application first."
    )


def removable(tracked: List[str]) -> List[str]:
    """The tracked files of a checkout a folder deploy removes before it copies the folder."""
    return [path for path in tracked if not KEPT.match(path)]


def _git(args: List[str], cwd: str, env: Dict[str, str], what: str) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Could not {what}: {result.stderr.decode(errors='replace').strip()}")
    return result


def fetch_folder(template_url: str, ref: str, template_path: str, work_dir: str, env: Dict[str, str]) -> str:
    """Fetch the repository at ref into work_dir and return the folder's content as a tar archive file.

    Raises RuntimeError when the repository cannot be read or has no such
    folder at that ref.
    """
    folder_segments(template_path)
    repo = Path(work_dir, "repo")
    repo.mkdir()
    _git(["init", "--quiet"], str(repo), env, f"prepare the fetch of {template_url}")
    _git(
        ["fetch", "--quiet", "--depth", "1", template_url, ref],
        str(repo), env, f"fetch {template_url} at {ref}",
    )
    kind = subprocess.run(
        ["git", "cat-file", "-t", f"FETCH_HEAD:{template_path}"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=60,
    )
    if kind.returncode != 0 or kind.stdout.strip() != "tree":
        raise RuntimeError(f"{template_url} has no folder '{template_path}' at {ref}")
    archive = str(Path(work_dir, "folder.tar"))
    _git(
        ["archive", "--format=tar", "-o", archive, f"FETCH_HEAD:{template_path}"],
        str(repo), env, f"read the folder {template_path} of {template_url}",
    )
    return archive


def replace_checkout(checkout: str, archive: str) -> None:
    """Make the checkout's files the archive's: tracked files are removed first, then the archive is unpacked.

    A checkout that is not a git repository yet (a first deploy) only
    receives the files.
    """
    target = Path(checkout)
    target.mkdir(parents=True, exist_ok=True)
    if Path(target, ".git", "HEAD").exists():
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=checkout, capture_output=True, timeout=60,
        )
        if listed.returncode != 0:
            raise RuntimeError(
                f"Could not list the files of {checkout}: {listed.stderr.decode(errors='replace').strip()}"
            )
        tracked = [p for p in listed.stdout.decode().split("\0") if p]
        for path in removable(tracked):
            file = Path(target, path)
            if file.is_file() or file.is_symlink():
                file.unlink()
    unpacked = subprocess.run(
        ["tar", "-xf", archive, "-C", checkout], capture_output=True, text=True, timeout=300,
    )
    if unpacked.returncode != 0:
        raise RuntimeError(f"Could not copy the folder into {checkout}: {unpacked.stderr.strip()}")
