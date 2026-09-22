# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
Shared metadata fetcher for thinkube-control.

Fetches catalog JSON files from two sources:
1. thinkube/thinkube-metadata (platform catalog, public)
2. {GITHUB_USERNAME}/{GITHUB_USERNAME}-metadata (user catalog, private, authenticated)

Merges the results and keeps them in memory for five minutes. When a fetch
fails and no fresh copy is in memory, CatalogUnavailableError is raised with
the URL and the failure.

The user metadata repository is created on the first template publish and
holds only the files that have entries, so HTTP 404 for a user catalog file
means the user has no entries in that catalog.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_PLATFORM_ORG = "thinkube"
_PLATFORM_METADATA_REPO = "thinkube-metadata"
_CACHE_TTL: float = 300  # 5 minutes

# Per-catalog memory cache: {catalog_name: {"data": ..., "time": float}}
_memory_cache: Dict[str, Dict[str, Any]] = {}


class CatalogUnavailableError(Exception):
    """A metadata catalog could not be fetched or read."""


def _github_raw_url(org: str, repo: str, filename: str) -> str:
    return f"https://raw.githubusercontent.com/{org}/{repo}/main/{filename}"


def _fetch_json(url: str, token: Optional[str] = None, timeout: int = 10) -> dict:
    """Fetch a JSON file from a URL, optionally with GitHub token auth.

    Raises urllib.error.HTTPError for an HTTP error status and
    CatalogUnavailableError for any other failure.
    """
    headers = {"User-Agent": "thinkube-control"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError:
        raise
    except Exception as e:
        raise CatalogUnavailableError(f"Cannot fetch catalog {url}: {e}") from e
    try:
        return json.loads(body)
    except ValueError as e:
        raise CatalogUnavailableError(f"Catalog {url} is not valid JSON: {e}") from e


def _extract(data: dict, key: str, url: str, merge_strategy: str) -> Union[List, Dict]:
    """The catalog entries under key, checked against the merge strategy's type."""
    if not isinstance(data, dict) or key not in data:
        raise CatalogUnavailableError(f"Catalog {url} has no '{key}' key")
    items = data[key]
    expected = list if merge_strategy == "list" else dict
    if not isinstance(items, expected):
        raise CatalogUnavailableError(
            f"Catalog {url}: '{key}' is {type(items).__name__}, expected {expected.__name__}"
        )
    return items


def _merge_list(platform: List[Dict], user: List[Dict], dedup_key: str) -> List[Dict]:
    """Merge two lists, user entries win on dedup_key collision."""
    seen = {}
    for item in platform:
        key = item.get(dedup_key)
        if key:
            seen[key] = item
    for item in user:
        key = item.get(dedup_key)
        if key:
            seen[key] = item  # user wins
    # Preserve order: platform first, then user-only entries
    result = []
    result_keys = set()
    for item in platform:
        key = item.get(dedup_key)
        if key and key in seen:
            result.append(seen[key])  # may be overridden by user
            result_keys.add(key)
    for item in user:
        key = item.get(dedup_key)
        if key and key not in result_keys:
            result.append(item)
            result_keys.add(key)
    return result


def _merge_dict(platform: Dict, user: Dict) -> Dict:
    """Merge two dicts, user keys override platform keys."""
    merged = dict(platform)
    merged.update(user)
    return merged


def _get_github_config():
    """Get the GitHub username and token from the environment."""
    github_username = os.environ.get("GITHUB_USERNAME", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    return github_username, github_token


def fetch_merged_catalog(
    catalog_name: str,
    file_name: str,
    extract_key: str,
    merge_strategy: str = "list",
    dedup_key: str = "id",
) -> Union[List, Dict]:
    """
    Fetch a catalog from platform + user metadata repos, merge, and cache.

    Args:
        catalog_name: Cache key (e.g. "models", "repositories", "optional_components")
        file_name: JSON filename in the metadata repo (e.g. "models.json")
        extract_key: Key to extract from the JSON (e.g. "models", "repositories", "components")
        merge_strategy: "list" for list append+dedup, "dict" for dict merge
        dedup_key: Key to deduplicate list entries by (default "id")

    Returns:
        Merged catalog data (list or dict depending on merge_strategy)

    Raises:
        CatalogUnavailableError: a catalog could not be fetched or read and no
            fresh copy is in memory.
    """
    now = time.time()
    cache_entry = _memory_cache.get(catalog_name)
    if cache_entry and (now - cache_entry["time"]) < _CACHE_TTL:
        return cache_entry["data"]

    github_username, github_token = _get_github_config()

    # Platform catalog (public, no auth)
    platform_url = _github_raw_url(_PLATFORM_ORG, _PLATFORM_METADATA_REPO, file_name)
    try:
        platform_data = _fetch_json(platform_url)
    except urllib.error.HTTPError as e:
        raise CatalogUnavailableError(
            f"Cannot fetch catalog {platform_url}: HTTP {e.code} {e.reason}"
        ) from e
    platform_items = _extract(platform_data, extract_key, platform_url, merge_strategy)
    if merge_strategy == "list":
        for item in platform_items:
            item.setdefault("_source", "platform")

    merged = platform_items
    # User catalog (private, with auth)
    if github_username and github_token:
        user_repo = f"{github_username}-metadata"
        user_url = _github_raw_url(github_username, user_repo, file_name)
        user_items = None
        try:
            user_data = _fetch_json(user_url, token=github_token)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise CatalogUnavailableError(
                    f"Cannot fetch catalog {user_url}: HTTP {e.code} {e.reason}"
                ) from e
            logger.info(f"No {catalog_name} entries in user metadata ({user_url}: HTTP 404)")
        else:
            user_items = _extract(user_data, extract_key, user_url, merge_strategy)
            if merge_strategy == "list":
                for item in user_items:
                    item.setdefault("_source", "user")
            logger.info(
                f"Fetched {catalog_name} from user metadata ({github_username}/{user_repo}): "
                f"{len(user_items)} entries"
            )
        if user_items is not None:
            if merge_strategy == "list":
                merged = _merge_list(platform_items, user_items, dedup_key)
            else:
                merged = _merge_dict(platform_items, user_items)

    _memory_cache[catalog_name] = {"data": merged, "time": now}
    logger.info(f"Fetched {catalog_name} catalog: {len(merged)} total entries")
    return merged
