# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Universal Dependencies treebanks, as the CoNLL-U files of their GitHub
repositories at a release tag.

Each treebank is a repository of github.com/UniversalDependencies, such as
UD_German-GSD, and each release is a tag, such as r2.18. Its files are named
<code>-ud-<split>[-<part>].conllu: the split is train, dev or test, and a
large split comes in parts (UD_German-HDT's train is four files). The
treebank's licence is the `License:` line of its README's metadata, or the
first line of its LICENSE.txt when the README has none.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

CONTENTS = "https://api.github.com/repos/UniversalDependencies/{treebank}/contents"
TIMEOUT = httpx.Timeout(60.0)
TRANSFER = httpx.Timeout(60.0, read=300.0)
SPLITS = ("train", "dev", "test")
TREEBANK = re.compile(r"^UD_[A-Za-z0-9_]+-[A-Za-z0-9_]+$")
SPLIT_FILE = re.compile(r"-ud-(train|dev|test)(?:-[\w.-]+)?\.conllu$")
LICENSE_LINE = re.compile(r"^\s*licen[cs]e\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
READMES = ("README.md", "README.txt", "README")


def _client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """The client of every call to GitHub."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


class UdError(Exception):
    """GitHub refused, or a treebank or its files are not there; the message names which."""


async def _get(client: httpx.AsyncClient, url: str, what: str, **params) -> httpx.Response:
    try:
        r = await client.get(url, params=params or None)
    except httpx.HTTPError as e:
        raise UdError(f"GitHub did not answer for {what}: {e}")
    return r


async def listing(treebank: str, tag: str) -> Dict[str, Any]:
    """The treebank's CoNLL-U files by split, in file-name order, and its licence."""
    async with _client(TIMEOUT) as client:
        r = await _get(client, CONTENTS.format(treebank=treebank), f"{treebank} at {tag}", ref=tag)
        if r.status_code == 404:
            said = _message(r)
            if "No commit found" in said:
                raise UdError(f"UniversalDependencies/{treebank} has no tag {tag}")
            raise UdError(f"UniversalDependencies has no treebank {treebank}: GitHub answered {said}")
        if r.status_code >= 400:
            raise UdError(f"GitHub answered {r.status_code} for {treebank} at {tag}: {_message(r)}")
        entries = [e for e in r.json() if e.get("type") == "file"]
        files: Dict[str, List[Dict[str, str]]] = {}
        for entry in sorted(entries, key=lambda e: e["name"]):
            found = SPLIT_FILE.search(entry["name"])
            if found:
                files.setdefault(found.group(1), []).append({"name": entry["name"], "url": entry["download_url"]})
        if not files:
            raise UdError(f"UniversalDependencies/{treebank} at {tag} has no -ud-train, -ud-dev or -ud-test .conllu file")
        licence = await _licence(client, treebank, tag, {e["name"]: e["download_url"] for e in entries})
    return {"treebank": treebank, "files": {s: files[s] for s in SPLITS if s in files}, "licence": licence}


async def _licence(client: httpx.AsyncClient, treebank: str, tag: str, urls: Dict[str, str]) -> str:
    for readme in READMES:
        if readme in urls:
            r = await _get(client, urls[readme], f"the README of {treebank}")
            if r.status_code == 200:
                found = LICENSE_LINE.search(r.text)
                if found:
                    return found.group(1)
    if "LICENSE.txt" in urls:
        r = await _get(client, urls["LICENSE.txt"], f"the LICENSE.txt of {treebank}")
        if r.status_code == 200:
            first = next((line.strip() for line in r.text.splitlines() if line.strip()), None)
            if first:
                return first
    raise UdError(f"UniversalDependencies/{treebank} at {tag} states no licence: its README has no License: line "
                  "and it has no LICENSE.txt")


def by_split(listings: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """Each split's files across the treebanks, in the order the treebanks were given; a split no treebank has
    is left out."""
    out: Dict[str, List[Dict[str, str]]] = {}
    for split in SPLITS:
        files = [{"url": f["url"], "name": f["name"], "treebank": t["treebank"]}
                 for t in listings for f in t["files"].get(split, [])]
        if files:
            out[split] = files
    return out


def description(listings: List[Dict[str, Any]], tag: str) -> str:
    return f"Universal Dependencies {tag}. " + "; ".join(
        f"{t['treebank']}: licence {t['licence']}" for t in listings)


def _message(r: httpx.Response) -> str:
    try:
        return str(r.json().get("message", r.text))
    except ValueError:
        return r.text[:300]


async def download(url: str, target: Path) -> None:
    async with _client(TRANSFER) as client:
        try:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise UdError(f"GitHub answered {r.status_code} for {url}")
                with target.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise UdError(f"the download of {url} failed: {e}")
