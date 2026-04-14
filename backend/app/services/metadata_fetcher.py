"""
Shared metadata fetcher for thinkube-control.

Fetches catalog JSON files from two sources:
1. thinkube/thinkube-metadata (platform catalog, public)
2. {GITHUB_ORG}/{GITHUB_ORG}-metadata (user catalog, private, authenticated)

Merges results and caches with the standard fallback chain:
memory cache (5min TTL) → fetch → stale memory → persistent cache → bundled fallback.
"""

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_PLATFORM_ORG = "thinkube"
_PLATFORM_METADATA_REPO = "thinkube-metadata"
_CACHE_TTL: float = 300  # 5 minutes

_PERSISTENT_CACHE_DIR = Path(
    os.getenv("THINKUBE_CACHE_DIR", "/home/thinkube/.cache/thinkube-control")
)

# Per-catalog memory cache: {catalog_name: {"data": ..., "time": float}}
_memory_cache: Dict[str, Dict[str, Any]] = {}


def _github_raw_url(org: str, repo: str, filename: str) -> str:
    return f"https://raw.githubusercontent.com/{org}/{repo}/main/{filename}"


def _fetch_json(url: str, token: Optional[str] = None, timeout: int = 10) -> Optional[dict]:
    """Fetch a JSON file from a URL, optionally with GitHub token auth."""
    headers = {"User-Agent": "thinkube-control"}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
        return None


def _save_persistent_cache(filename: str, data: Any) -> None:
    try:
        _PERSISTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = _PERSISTENT_CACHE_DIR / filename
        with open(cache_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed to save persistent cache {filename}: {e}")


def _load_persistent_cache(filename: str) -> Optional[dict]:
    cache_path = _PERSISTENT_CACHE_DIR / filename
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load persistent cache {filename}: {e}")
    return None


def _load_bundled(bundled_path: Optional[Path], extract_key: str) -> Any:
    if bundled_path and bundled_path.exists():
        try:
            with open(bundled_path) as f:
                data = json.load(f)
                return data.get(extract_key, [] if isinstance(data.get(extract_key), list) else {})
        except Exception as e:
            logger.warning(f"Failed to load bundled fallback {bundled_path}: {e}")
    return None


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
    """Get GitHub org and token from environment (via Settings or direct env)."""
    github_org = os.environ.get("GITHUB_ORG", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    return github_org, github_token


def fetch_merged_catalog(
    catalog_name: str,
    file_name: str,
    extract_key: str,
    bundled_path: Optional[Path],
    merge_strategy: str = "list",
    dedup_key: str = "id",
) -> Union[List, Dict]:
    """
    Fetch a catalog from platform + user metadata repos, merge, and cache.

    Args:
        catalog_name: Cache key (e.g. "models", "repositories", "optional_components")
        file_name: JSON filename in the metadata repo (e.g. "models.json")
        extract_key: Key to extract from the JSON (e.g. "models", "repositories", "components")
        bundled_path: Path to bundled fallback JSON file (or None)
        merge_strategy: "list" for list append+dedup, "dict" for dict merge
        dedup_key: Key to deduplicate list entries by (default "id")

    Returns:
        Merged catalog data (list or dict depending on merge_strategy)
    """
    global _memory_cache

    now = time.time()
    cache_entry = _memory_cache.get(catalog_name)
    if cache_entry and (now - cache_entry["time"]) < _CACHE_TTL:
        return cache_entry["data"]

    github_org, github_token = _get_github_config()

    # Fetch platform catalog (public, no auth)
    platform_url = _github_raw_url(_PLATFORM_ORG, _PLATFORM_METADATA_REPO, file_name)
    platform_data = _fetch_json(platform_url)
    platform_items = platform_data.get(extract_key, [] if merge_strategy == "list" else {}) if platform_data else None

    # Fetch user catalog (private, with auth)
    user_items = None
    if github_org and github_token:
        user_repo = f"{github_org}-metadata"
        user_url = _github_raw_url(github_org, user_repo, file_name)
        user_data = _fetch_json(user_url, token=github_token)
        if user_data:
            user_items = user_data.get(extract_key, [] if merge_strategy == "list" else {})
            logger.info(
                f"Fetched {catalog_name} from user metadata ({github_org}/{user_repo}): "
                f"{len(user_items)} entries"
            )

    # Merge if we got fresh data from at least the platform
    if platform_items is not None:
        if user_items is not None:
            if merge_strategy == "list":
                merged = _merge_list(platform_items, user_items, dedup_key)
            else:
                merged = _merge_dict(platform_items, user_items)
        else:
            merged = platform_items

        _memory_cache[catalog_name] = {"data": merged, "time": now}
        # Save combined data to persistent cache
        cache_data = {extract_key: merged}
        if platform_data:
            cache_data["version"] = platform_data.get("version", "1.0.0")
        _save_persistent_cache(file_name, cache_data)
        logger.info(f"Fetched {catalog_name} catalog: {len(merged)} total entries")
        return merged

    # If platform fetch failed but user succeeded, use user data alone
    if user_items is not None:
        _memory_cache[catalog_name] = {"data": user_items, "time": now}
        _save_persistent_cache(file_name, {extract_key: user_items})
        logger.info(f"Using user-only {catalog_name} catalog: {len(user_items)} entries")
        return user_items

    # Fallback to stale memory cache
    if cache_entry:
        logger.info(f"Using stale cached {catalog_name} catalog")
        return cache_entry["data"]

    # Fallback to persistent cache
    persistent = _load_persistent_cache(file_name)
    if persistent:
        items = persistent.get(extract_key, [] if merge_strategy == "list" else {})
        if items:
            _memory_cache[catalog_name] = {"data": items, "time": now}
            logger.info(f"Using persistent cached {catalog_name} catalog: {len(items)} entries")
            return items

    # Final fallback to bundled copy
    bundled = _load_bundled(bundled_path, extract_key)
    if bundled:
        _memory_cache[catalog_name] = {"data": bundled, "time": now}
        logger.info(f"Using bundled {catalog_name} fallback: {len(bundled)} entries")
        return bundled

    empty = [] if merge_strategy == "list" else {}
    logger.error(f"No {catalog_name} catalog available")
    _memory_cache[catalog_name] = {"data": empty, "time": now}
    return empty
