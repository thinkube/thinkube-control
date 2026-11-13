# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The folder dockerfiles of the IDE's home, where image definitions are
edited with Thinkube IDE. This backend mounts it at /storage/dockerfiles
(filesystems in thinkube.yaml); Thinkube IDE sees it at
/home/thinkube/dockerfiles.

The backend writes as root and the IDE edits as its own user, so what the
backend creates is given to that user.
"""

import os
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path("/storage/dockerfiles")
IDE_ROOT = "/home/thinkube/dockerfiles"
# The user Thinkube IDE and Thinkube Control write the home as
IDE_USER = 1000
# The build context travels in the workflow as text, bounded by the size of one Kubernetes object
CONTEXT_LIMIT_BYTES = 900 * 1024


def custom_dir(name: str) -> Path:
    """The folder of a custom image."""
    return ROOT / "custom" / name


def mirrored_template(safe_name: str) -> Path:
    """The Dockerfile template of a mirrored base image."""
    return ROOT / "templates" / "mirrored" / f"{safe_name}.Dockerfile"


def ide_path(path: Path) -> str:
    """The path Thinkube IDE has for a path under the folder."""
    return f"{IDE_ROOT}/{path.relative_to(ROOT)}"


def editor_url(domain: str, folder: Path, file: Path) -> str:
    """The Thinkube IDE address that opens the folder with the file in an editor."""
    payload = f'[["openFile","vscode-remote://{ide_path(file)}"]]'
    return f"https://code.{domain}/?folder={ide_path(folder)}&payload={payload}"


def give_to_ide(path: Path) -> None:
    """Make path, and each folder above it up to the root, the IDE user's, writable by it."""
    path.relative_to(ROOT)
    current = path
    while True:
        os.chown(current, IDE_USER, IDE_USER)
        current.chmod(0o775 if current.is_dir() else 0o664)
        if current == ROOT:
            return
        current = current.parent


def write(path: Path, text: str) -> None:
    """Write a file under the folder, creating its folders, all owned by the IDE user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    give_to_ide(path)


def context_artifacts(context: Path) -> List[Dict[str, Any]]:
    """The files of an image's folder, as raw input artifacts of the build pod. ValueError names what cannot be sent."""
    artifacts: List[Dict[str, Any]] = []
    size = 0
    for path in sorted(p for p in context.rglob("*") if p.is_file()):
        relative = path.relative_to(context)
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            raise ValueError(f"{relative} is not a text file; a custom image folder can hold text files only")
        size += len(text.encode())
        artifacts.append({"name": f"file-{len(artifacts)}", "path": f"/context/{relative}", "raw": {"data": text}})
    if size > CONTEXT_LIMIT_BYTES:
        raise ValueError(f"{ide_path(context)} holds {size} bytes; a custom image folder can hold "
                         f"{CONTEXT_LIMIT_BYTES} at most")
    return artifacts
