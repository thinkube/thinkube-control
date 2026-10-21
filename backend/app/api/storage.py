# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Storage: shared folders and their quotas, the disk under them, node disks
and volume claims."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.api_tokens import get_current_user_dual_auth
from app.services import storage

router = APIRouter()


class QuotaRequest(BaseModel):
    filesystem: str = Field(..., description="The JuiceFS file system, for example 'thinkube-shared'.")
    folder: str = Field(..., description="The top-level folder that has the quota, as get_storage names it.")
    size_gib: int = Field(..., description="The new quota in GiB.")


class DeleteRequest(BaseModel):
    filesystem: str = Field(..., description="The JuiceFS file system, for example 'thinkube-shared'.")
    path: str = Field(..., description="The file or folder to delete, inside a top-level folder, "
                                       "for example 'pvc-7ef8.../kubexlat/qwen-lora-run3'.")


def _run(fn, *args):
    try:
        return fn(*args)
    except storage.StorageError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/storage", operation_id="get_storage")
def get_storage(current_user: dict = Depends(get_current_user_dual_auth)) -> Dict[str, Any]:
    """The shared JuiceFS file systems: each top-level folder with its size in bytes, its quota (null when
    it has none), the volume claims that mount it, and quota_at_next_mount_bytes, the PV capacity the JuiceFS
    driver sets as the quota when the volume is mounted again. Also each file system's trash days (a deleted
    file frees the folder's quota at once and the disk after that many days) and the SeaweedFS disks that
    hold the data, with total, used and free bytes."""
    return _run(storage.shared)


@router.get("/storage/folder", operation_id="list_storage_folder")
def list_storage_folder(
    filesystem: str = Query(..., description="The JuiceFS file system, for example 'thinkube-shared'."),
    path: str = Query(..., description="The folder, relative to the file system's root."),
    current_user: dict = Depends(get_current_user_dual_auth),
) -> Dict[str, Any]:
    """The entries of one folder of a shared file system, largest first, each with its size in bytes and
    its numbers of folders and files."""
    return _run(storage.folder, filesystem, path)


@router.post("/storage/quota", operation_id="set_storage_quota")
def set_storage_quota(request: QuotaRequest, current_user: dict = Depends(get_current_user_dual_auth)) -> Dict[str, Any]:
    """Set the quota of a top-level shared folder that has one. Also sets the capacity of the folder's
    dynamically provisioned PV, which the JuiceFS driver applies as the quota at every mount. Refused below
    what the folder holds, or when the room it leaves the folder is more than the free space of the disks
    under it. Takes effect at once; nothing restarts."""
    return _run(storage.set_quota, request.filesystem, request.folder, request.size_gib)


@router.post("/storage/delete", operation_id="delete_storage_path")
def delete_storage_path(request: DeleteRequest, current_user: dict = Depends(get_current_user_dual_auth)) -> Dict[str, Any]:
    """Delete a file or folder inside a top-level shared folder; a top-level folder itself belongs to a
    volume and is refused. Answers the bytes freed. The folder's quota is free at once; the disk under it
    after the file system's trash days. Cannot be undone from here."""
    return _run(storage.delete, request.filesystem, request.path)


@router.get("/storage/nodes", operation_id="get_storage_nodes")
def get_storage_nodes(current_user: dict = Depends(get_current_user_dual_auth)) -> Dict[str, Any]:
    """Each node's disks (total, used and free bytes per mounted disk) and its training folder
    (/var/lib/thinkube-training): its size and the size of each folder two levels down. A node whose
    node-metrics pod does not answer has an error instead."""
    return _run(storage.nodes)


@router.get("/storage/claims", operation_id="get_storage_claims")
def get_storage_claims(current_user: dict = Depends(get_current_user_dual_auth)) -> Dict[str, Any]:
    """Every volume claim with its namespace, storage class, requested and bound size and, where the
    kubelet measures it, the bytes it uses (null where it does not)."""
    return _run(storage.claims)
