# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A Hugging Face dataset, as the Parquet files the Hub publishes for it.

The Hub's dataset viewer converts every public dataset to Parquet and lists
the files by configuration and split. A split can be several files; the
import downloads them and writes one Parquet file per split.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

LISTING = "https://datasets-server.huggingface.co/parquet"
TIMEOUT = httpx.Timeout(60.0)
TRANSFER = httpx.Timeout(60.0, read=300.0)
# The import downloads to the pod's disk and uploads to MLflow
MAX_BYTES = 4 * 1024 ** 3


class HubError(Exception):
    """The Hub refused or failed; the message says why."""


async def files(repo: str) -> List[Dict[str, Any]]:
    """The Parquet files of a dataset: each with its config, split, url and size."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.get(LISTING, params={"dataset": repo})
        except httpx.HTTPError as e:
            raise HubError(f"the Hugging Face dataset viewer did not answer: {e}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", r.text)
        except ValueError:
            detail = r.text
        raise HubError(f"Hugging Face has no Parquet files for {repo}: {detail}")
    listed = r.json().get("parquet_files", [])
    if not listed:
        raise HubError(f"Hugging Face lists no Parquet files for {repo}")
    return listed


def configs(listed: List[Dict[str, Any]]) -> List[str]:
    return sorted({f["config"] for f in listed})


def choose(listed: List[Dict[str, Any]], config: Optional[str], splits: Optional[List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """The files of each wanted split of one config, refused with what there is."""
    available = configs(listed)
    if config is None:
        if len(available) > 1:
            raise HubError(f"the dataset has the configs {available}; choose one")
        config = available[0]
    if config not in available:
        raise HubError(f"the dataset has no config {config}; it has {available}")
    by_split: Dict[str, List[Dict[str, Any]]] = {}
    for f in listed:
        if f["config"] == config:
            by_split.setdefault(f["split"], []).append(f)
    if splits:
        missing = [s for s in splits if s not in by_split]
        if missing:
            raise HubError(f"config {config} has no split {missing}; it has {sorted(by_split)}")
        by_split = {s: by_split[s] for s in splits}
    total = sum(f.get("size", 0) for fs in by_split.values() for f in fs)
    if total > MAX_BYTES:
        raise HubError(f"the chosen splits are {total / 1024 ** 3:.1f} GiB; an import can be at most "
                       f"{MAX_BYTES / 1024 ** 3:.0f} GiB. Choose fewer splits")
    return by_split


async def download(url: str, target: Path) -> None:
    async with httpx.AsyncClient(timeout=TRANSFER, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise HubError(f"Hugging Face answered {r.status_code} for {url}")
                with target.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise HubError(f"the download of {url} failed: {e}")
