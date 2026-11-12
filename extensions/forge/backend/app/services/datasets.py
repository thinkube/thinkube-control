# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The datasets of the Datasets extension, which Forge trains on.

Datasets runs beside Forge in Thinkube Control's namespace. Forge calls its
backend there with the signed-in user's token, so Datasets applies the
user's rights. A dataset's splits are Parquet files among MLflow's
artifacts, which a notebook server mounts at ~/thinkube/mlflow.
"""

from typing import Any, Dict, List, Optional

import httpx

DATASETS_API = "http://datasets-backend:8000/api/v1/datasets"
TIMEOUT = httpx.Timeout(60.0)


class DatasetsError(Exception):
    """Datasets refused or failed a call; `status` is its HTTP status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def _call(token: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.get(f"{DATASETS_API}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise DatasetsError(502, f"Datasets did not answer GET {path}: {e}. Install the Datasets extension")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise DatasetsError(r.status_code, f"Datasets answered GET {path} with {r.status_code}: {detail}")
    return r.json()


def _trainable(described: Dict[str, Any]) -> bool:
    return described["status"] == "ready" and bool(described.get("mapping"))


async def trainable(token: str) -> List[Dict[str, Any]]:
    """The datasets a run can use, each described by its latest ready version
    (`version`): imported, with its columns mapped."""
    return [d for d in await _call(token, "") if _trainable(d)]


async def versions(token: str, dataset_id: str) -> List[Dict[str, Any]]:
    """The versions of a dataset a run can use, the newest first."""
    return [v for v in await _call(token, f"/{dataset_id}/versions") if _trainable(v)]


async def training(token: str, dataset_id: str, version: Optional[int]) -> Dict[str, Any]:
    """The splits' files and the mapping of a version of a dataset, as a
    training reads them, and the version they are; the latest ready version
    when none is given."""
    found = await _call(token, f"/{dataset_id}/training", {"version": version} if version is not None else None)
    if "version" not in found:
        raise DatasetsError(502, f"Datasets did not say which version of dataset {dataset_id} it gave; "
                                 "update the Datasets extension")
    return found


async def first_example(token: str, dataset_id: str, version: int, split: str) -> Optional[Dict[str, Any]]:
    """The first row of a split of a version as the trainer gets it, or None when it does not fit the mapping."""
    shown = await _call(token, f"/{dataset_id}/rows", {"version": version, "split": split, "limit": 1})
    examples = shown.get("examples") or []
    return examples[0].get("example") if examples else None


async def version(token: str, dataset_id: str, number: int) -> Dict[str, Any]:
    """A version of a dataset: its state, splits, columns and mapping."""
    return await _call(token, f"/{dataset_id}/versions/{number}")
