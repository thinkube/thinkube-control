# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The folder of a JuiceFS volume claim, as this pod sees it.

The shared file system thinkube-shared is mounted in this pod at
/storage/thinkube-shared, a static PV of the file system's root. A notebook
job writes its notebook into the folder of the notebooks claim through it.
The Storage page and its operations are the control-storage extension's.
"""

import base64
import re
import urllib.parse
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

JUICEFS_DRIVER = "csi.juicefs.com"
# The secrets of the JuiceFS file systems, by which a volume's file system is
# known; a file system mounted here is at MOUNT_ROOT/<name of the file system>.
FILESYSTEM_SECRETS = (("juicefs", "juicefs-secret"), ("juicefs", "juicefs-mlflow-secret"))
MOUNT_ROOT = Path("/storage")

GIB = 1024 ** 3


class FolderError(Exception):
    """The folder of a claim could not be found; the message says why."""


def _core() -> client.CoreV1Api:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


def _filesystems(core: client.CoreV1Api) -> Dict[str, Dict[str, Any]]:
    """The JuiceFS file systems by name: the secret they come from, the
    metadata URL without its password, the password, and the mount."""
    found = {}
    for namespace, name in FILESYSTEM_SECRETS:
        try:
            data = core.read_namespaced_secret(name, namespace).data or {}
        except ApiException as e:
            raise FolderError(f"secret {namespace}/{name} of a JuiceFS file system: {e.reason}") from e
        fs_name = _b64(data, "name", namespace, name)
        url = urllib.parse.urlsplit(_b64(data, "metaurl", namespace, name))
        host = url.hostname + (f":{url.port}" if url.port else "")
        user = f"{url.username}@" if url.username else ""
        found[fs_name] = {
            "name": fs_name,
            "secret": f"{namespace}/{name}",
            "meta": urllib.parse.urlunsplit((url.scheme, user + host, url.path, url.query, url.fragment)),
            "password": url.password or "",
            "mount": MOUNT_ROOT / fs_name,
        }
    return found


def _b64(data: Dict[str, str], key: str, namespace: str, name: str) -> str:
    if key not in data:
        raise FolderError(f"secret {namespace}/{name} has no '{key}'")
    return base64.b64decode(data[key]).decode()


def _inside(fs: Dict[str, Any], rel: str) -> Path:
    """The mounted path of `rel`, refusing anything that leaves the file system."""
    parts = PurePosixPath("/" + rel).parts[1:]
    if any(p in ("..", ".") for p in parts):
        raise FolderError(f"path {rel!r} must not contain '.' or '..'")
    return fs["mount"].joinpath(*parts)


def _juicefs_volumes(core: client.CoreV1Api, filesystems: Dict[str, Dict[str, Any]]):
    """The JuiceFS PVs by (file system, folder): each with its claim and,
    for a dynamically provisioned one, the capacity the driver turns into the
    folder quota. A PV without a subPath mounts the root, folder ""."""
    by_secret = {fs["secret"]: name for name, fs in filesystems.items()}
    volumes: Dict[tuple, List[Dict[str, Any]]] = {}
    for pv in core.list_persistent_volume().items:
        csi = pv.spec.csi
        if not csi or csi.driver != JUICEFS_DRIVER or not csi.node_publish_secret_ref:
            continue
        ref = csi.node_publish_secret_ref
        attrs = csi.volume_attributes or {}
        folder = attrs.get("subPath", "")
        fs_name = by_secret.get(f"{ref.namespace}/{ref.name}")
        if fs_name is None:
            # A per-volume secret in kube-system names its file system in "name".
            try:
                data = core.read_namespaced_secret(ref.name, ref.namespace).data or {}
            except ApiException as e:
                raise FolderError(f"secret {ref.namespace}/{ref.name} of volume {pv.metadata.name}: "
                                   f"{e.reason}") from e
            fs_name = _b64(data, "name", ref.namespace, ref.name)
        claim = pv.spec.claim_ref
        volumes.setdefault((fs_name, folder), []).append({
            "volume": pv.metadata.name,
            "claim": f"{claim.namespace}/{claim.name}" if claim else None,
            "capacity_bytes": _quantity(pv.spec.capacity.get("storage")) if pv.spec.capacity else None,
            "sets_quota": "capacity" in attrs,
        })
    return volumes


def _quantity(q: Optional[str]) -> Optional[int]:
    if q is None:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGTPE]i?)?", q)
    if not m:
        raise FolderError(f"storage quantity {q!r} is not known how to read")
    factor = {None: 1, "Ki": 1024, "Mi": 1024 ** 2, "Gi": GIB, "Ti": 1024 ** 4, "Pi": 1024 ** 5,
              "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4, "P": 1000 ** 5}[m.group(2)]
    return int(float(m.group(1)) * factor)


def claim_folder(claim: str) -> Path:
    """The path in this pod of the folder a JuiceFS volume claim mounts,
    `claim` being "<namespace>/<name>"."""
    core = _core()
    filesystems = _filesystems(core)
    found = [(fs_name, folder) for (fs_name, folder), vols in _juicefs_volumes(core, filesystems).items()
             if any(v["claim"] == claim for v in vols)]
    if len(found) != 1:
        raise FolderError(f"volume claim {claim} mounts {len(found)} JuiceFS folders; one was expected")
    fs_name, folder = found[0]
    path = _inside(filesystems[fs_name], folder)
    if not path.is_dir():
        raise FolderError(f"the folder of volume claim {claim}, {fs_name}:/{folder}, is not mounted here")
    return path
