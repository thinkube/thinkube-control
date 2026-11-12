# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Storage: the shared JuiceFS folders and their quotas, the disk under them,
the nodes' own disks and the volume claims of the apps.

Each JuiceFS file system is mounted in this pod at /storage/<name>, a static
PV of the file system's root. The juicefs tool of the CSI driver's image reads
and changes it: sizes with `juicefs summary`, quotas with `juicefs quota`,
deletion with `juicefs rmr`. The CSI driver sets the quota of a dynamically
provisioned volume's folder again from the PV's capacity at every mount, so a
quota change sets that capacity too.

A deleted file goes to the file system's trash: the folder's quota is free at
once, the disk under it when the trash is emptied after the file system's
trash days.
"""

import base64
import csv
import io
import json
import os
import re
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

import httpx
from kubernetes import client, config
from kubernetes.client.rest import ApiException

JUICEFS = "/usr/local/bin/juicefs"
JUICEFS_DRIVER = "csi.juicefs.com"
# The secrets of the JuiceFS file systems; each one's file system is mounted
# at MOUNT_ROOT/<name of the file system>.
FILESYSTEM_SECRETS = (("juicefs", "juicefs-secret"), ("juicefs", "juicefs-mlflow-secret"))
MOUNT_ROOT = Path("/storage")

SEAWEEDFS_NAMESPACE = "seaweedfs"
SEAWEEDFS_VOLUME_SELECTOR = "app.kubernetes.io/component=volume"
SEAWEEDFS_VOLUME_PORT = 8080

NODE_METRICS_NAMESPACE = "thinkube-control"
NODE_METRICS_PORT = 9100
# du of a training folder runs on the node; it reads the whole tree.
NODE_STORAGE_TIMEOUT_SECONDS = 120

GIB = 1024 ** 3
_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": GIB, "TiB": 1024 ** 4, "PiB": 1024 ** 5}


class StorageError(Exception):
    """A storage operation could not do what was asked; the message says what."""


def _core() -> client.CoreV1Api:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


# --- JuiceFS -------------------------------------------------------------

def _filesystems(core: client.CoreV1Api) -> Dict[str, Dict[str, Any]]:
    """The JuiceFS file systems by name: the secret they come from, the
    metadata URL without its password, the password, and the mount."""
    found = {}
    for namespace, name in FILESYSTEM_SECRETS:
        try:
            data = core.read_namespaced_secret(name, namespace).data or {}
        except ApiException as e:
            raise StorageError(f"secret {namespace}/{name} of a JuiceFS file system: {e.reason}") from e
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
        raise StorageError(f"secret {namespace}/{name} has no '{key}'")
    return base64.b64decode(data[key]).decode()


# Stands for the file system's metadata URL in the arguments of _juicefs.
META = object()


def _juicefs(fs: Dict[str, Any], *args: Any, timeout: int = 120) -> str:
    """Runs the juicefs tool. The password goes in META_PASSWORD, not on the
    command line."""
    argv = [JUICEFS] + [fs["meta"] if a is META else a for a in args]
    env = {**os.environ, "META_PASSWORD": fs["password"]}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        raise StorageError(f"juicefs {args[0]} on {fs['name']} did not finish in {timeout} s") from e
    if done.returncode != 0:
        lines = (done.stderr or done.stdout).strip().splitlines()
        errors = [l for l in lines if "<FATAL>" in l or "<ERROR>" in l]
        detail = (errors or lines or [f"exit code {done.returncode}, no output"])[-1]
        raise StorageError(f"juicefs {args[0]} on {fs['name']} failed: {detail}")
    return done.stdout


def _size(text: str) -> int:
    m = re.fullmatch(r"([\d.]+)\s*([KMGTP]?i?B)", text.strip())
    if not m or m.group(2) not in _UNITS:
        raise StorageError(f"juicefs printed a size it is not known how to read: {text!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2)])


def _quotas(fs: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The folder quotas of a file system by folder path (without the leading /)."""
    out = _juicefs(fs, "quota", "list", META)
    quotas = {}
    for line in out.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].startswith("/"):
            continue
        quotas[cells[0].lstrip("/")] = {"quota_bytes": None if cells[1] == "unlimited" else _size(cells[1])}
    return quotas


def _trash_days(fs: Dict[str, Any]) -> int:
    status = json.loads(_juicefs(fs, "status", META))
    return int(status["Setting"].get("TrashDays", 0))


def _summary(fs: Dict[str, Any], rel: str) -> Dict[str, Any]:
    """Size, directories and files of a path and of each entry directly in it."""
    target = _inside(fs, rel)
    if not target.exists():
        raise StorageError(f"{fs['name']}:/{rel} does not exist")
    out = _juicefs(fs, "summary", "--depth", "1", "--entries", "100000", "--strict", "--csv", str(target))
    rows = list(csv.DictReader(io.StringIO(out)))
    if not rows:
        raise StorageError(f"juicefs summary printed nothing for {fs['name']}:/{rel}")
    def row(r: Dict[str, str]) -> Dict[str, Any]:
        return {"bytes": int(r["SIZE"]), "dirs": int(r["DIRS"]), "files": int(r["FILES"])}
    entries = []
    for r in rows[1:]:
        name = r["PATH"].rstrip("/")
        if not rel and name.startswith("."):
            continue
        entries.append({"name": name, "path": f"{rel}/{name}".lstrip("/"),
                        "is_dir": r["PATH"].endswith("/"), **row(r)})
    entries.sort(key=lambda e: e["bytes"], reverse=True)
    return {**row(rows[0]), "entries": entries}


def _inside(fs: Dict[str, Any], rel: str) -> Path:
    """The mounted path of `rel`, refusing anything that leaves the file system."""
    parts = PurePosixPath("/" + rel).parts[1:]
    if any(p in ("..", ".") for p in parts):
        raise StorageError(f"path {rel!r} must not contain '.' or '..'")
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
                raise StorageError(f"secret {ref.namespace}/{ref.name} of volume {pv.metadata.name}: "
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
        raise StorageError(f"storage quantity {q!r} is not known how to read")
    factor = {None: 1, "Ki": 1024, "Mi": 1024 ** 2, "Gi": GIB, "Ti": 1024 ** 4, "Pi": 1024 ** 5,
              "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4, "P": 1000 ** 5}[m.group(2)]
    return int(float(m.group(1)) * factor)


def _disk_under(core: client.CoreV1Api) -> List[Dict[str, Any]]:
    """The disks of the SeaweedFS volume servers, which hold the JuiceFS data."""
    pods = core.list_namespaced_pod(SEAWEEDFS_NAMESPACE, label_selector=SEAWEEDFS_VOLUME_SELECTOR).items
    disks = []
    for pod in pods:
        if pod.status.phase != "Running" or not pod.status.pod_ip:
            continue
        url = f"http://{pod.status.pod_ip}:{SEAWEEDFS_VOLUME_PORT}/status"
        try:
            status = httpx.get(url, timeout=10).json()
        except (httpx.HTTPError, ValueError) as e:
            raise StorageError(f"SeaweedFS volume server {pod.metadata.name} did not answer: {e}") from e
        for d in status.get("DiskStatuses", []):
            disks.append({"server": pod.metadata.name, "node": pod.spec.node_name, "dir": d["dir"],
                          "total_bytes": d["all"], "used_bytes": d["used"], "free_bytes": d["free"]})
    if not disks:
        raise StorageError("no running SeaweedFS volume server reported a disk")
    return disks


def shared() -> Dict[str, Any]:
    """Every JuiceFS file system with its top-level folders, their sizes,
    quotas and claims, and the disks under them."""
    core = _core()
    filesystems = _filesystems(core)
    volumes = _juicefs_volumes(core, filesystems)
    result = []
    for name, fs in filesystems.items():
        quotas = _quotas(fs)
        # Each top-level folder is measured on its own: the file system's
        # root also holds its internal folders, which only root may read.
        tops = sorted(e.name for e in os.scandir(fs["mount"]) if e.is_dir() and not e.name.startswith("."))
        with ThreadPoolExecutor(max_workers=8) as pool:
            sizes = list(pool.map(lambda t: _summary(fs, t), tops))
        folders = []
        for top, size in zip(tops, sizes):
            entry = {"name": top, "path": top, "is_dir": True,
                     "bytes": size["bytes"], "dirs": size["dirs"], "files": size["files"]}
            vols = volumes.get((name, entry["name"]), [])
            quota = quotas.get(entry["name"], {}).get("quota_bytes")
            # The capacity the CSI driver will set as the quota at the next mount.
            next_quota = [v["capacity_bytes"] for v in vols if v["sets_quota"]]
            folders.append({
                **entry,
                "quota_bytes": quota,
                "claims": sorted({v["claim"] for v in vols if v["claim"]}),
                "volumes": [v["volume"] for v in vols],
                "quota_at_next_mount_bytes": next_quota[0] if next_quota else None,
            })
        folders.sort(key=lambda f: f["bytes"], reverse=True)
        root = volumes.get((name, ""), [])
        result.append({"name": name, "used_bytes": sum(f["bytes"] for f in folders),
                       "root_claims": sorted({v["claim"] for v in root if v["claim"]}),
                       "trash_days": _trash_days(fs), "folders": folders})
    return {"filesystems": result, "disks": _disk_under(core)}


def folder(filesystem: str, path: str) -> Dict[str, Any]:
    """The entries of one folder of a file system, largest first."""
    fs = _fs(_core(), filesystem)
    return {"filesystem": filesystem, "path": path.strip("/"), **_summary(fs, path.strip("/"))}


def _fs(core: client.CoreV1Api, name: str) -> Dict[str, Any]:
    filesystems = _filesystems(core)
    if name not in filesystems:
        raise StorageError(f"no JuiceFS file system {name!r}; there are {', '.join(sorted(filesystems))}")
    return filesystems[name]


def claim_folder(claim: str) -> Path:
    """The path in this pod of the folder a JuiceFS volume claim mounts,
    `claim` being "<namespace>/<name>"."""
    core = _core()
    filesystems = _filesystems(core)
    found = [(fs_name, folder) for (fs_name, folder), vols in _juicefs_volumes(core, filesystems).items()
             if any(v["claim"] == claim for v in vols)]
    if len(found) != 1:
        raise StorageError(f"volume claim {claim} mounts {len(found)} JuiceFS folders; one was expected")
    fs_name, folder = found[0]
    path = _inside(filesystems[fs_name], folder)
    if not path.is_dir():
        raise StorageError(f"the folder of volume claim {claim}, {fs_name}:/{folder}, is not mounted here")
    return path


def set_quota(filesystem: str, folder_name: str, size_gib: int) -> Dict[str, Any]:
    """Sets the quota of a top-level folder that has one, on the folder and on
    the PVs whose capacity the CSI driver turns into it at every mount.

    Refused when the size is below what the folder holds, or when the room it
    leaves the folder is more than the free space of the disks under it."""
    core = _core()
    fs = _fs(core, filesystem)
    name = folder_name.strip("/")
    if "/" in name or not name:
        raise StorageError(f"{name!r} is not a top-level folder")
    quotas = _quotas(fs)
    if name not in quotas:
        raise StorageError(f"{filesystem}:/{name} has no quota")
    if size_gib < 1:
        raise StorageError("the quota must be at least 1 GiB")
    size = size_gib * GIB
    used = _summary(fs, name)["bytes"]
    if size < used:
        raise StorageError(f"{filesystem}:/{name} holds {used / GIB:.1f} GiB, more than {size_gib} GiB")
    free = sum(d["free_bytes"] for d in _disk_under(core))
    if size - used > free:
        raise StorageError(f"a quota of {size_gib} GiB leaves {filesystem}:/{name} room for "
                           f"{(size - used) / GIB:.0f} GiB more, and the disks under it have "
                           f"{free / GIB:.0f} GiB free")
    patched = []
    for v in _juicefs_volumes(core, {filesystem: fs}).get((filesystem, name), []):
        if v["sets_quota"]:
            core.patch_persistent_volume(v["volume"], {"spec": {"capacity": {"storage": f"{size_gib}Gi"}}})
            patched.append(v["volume"])
    _juicefs(fs, "quota", "set", META, "--path", f"/{name}", "--capacity", str(size_gib))
    return {"filesystem": filesystem, "folder": name, "quota_bytes": _quotas(fs)[name]["quota_bytes"],
            "used_bytes": used, "volumes_patched": patched}


def delete(filesystem: str, path: str) -> Dict[str, Any]:
    """Deletes a file or folder inside a top-level folder. A top-level folder
    belongs to a volume and is not deleted here."""
    fs = _fs(_core(), filesystem)
    rel = path.strip("/")
    parts = PurePosixPath("/" + rel).parts[1:]
    if len(parts) < 2:
        raise StorageError(f"{filesystem}:/{rel} is a top-level folder, which belongs to a volume; "
                           "only what is inside one is deleted here")
    if parts[0].startswith("."):
        raise StorageError(f"{filesystem}:/{rel} is inside a JuiceFS internal folder")
    target = _inside(fs, rel)
    if not target.exists():
        raise StorageError(f"{filesystem}:/{rel} does not exist")
    if target.is_dir():
        size = _summary(fs, rel)["bytes"]
        _juicefs(fs, "rmr", str(target), timeout=600)
    else:
        size = target.stat().st_size
        target.unlink()
    if target.exists():
        raise StorageError(f"{filesystem}:/{rel} is still there after the delete")
    return {"filesystem": filesystem, "path": rel, "freed_bytes": size, "trash_days": _trash_days(fs)}


# --- Nodes and claims ----------------------------------------------------

def _node_metrics_ips(core: client.CoreV1Api) -> Dict[str, str]:
    pods = core.list_namespaced_pod(NODE_METRICS_NAMESPACE, label_selector="app=node-metrics").items
    return {p.spec.node_name: p.status.pod_ip for p in pods
            if p.status.phase == "Running" and p.status.pod_ip}


def nodes() -> Dict[str, Any]:
    """Each node's disks and its training folder, as its node-metrics pod reads them."""
    core = _core()
    ips = _node_metrics_ips(core)
    names = sorted(n.metadata.name for n in core.list_node().items)

    def read(node: str) -> Dict[str, Any]:
        if node not in ips:
            return {"node": node, "error": f"no running node-metrics pod on {node}"}
        try:
            r = httpx.get(f"http://{ips[node]}:{NODE_METRICS_PORT}/storage", timeout=NODE_STORAGE_TIMEOUT_SECONDS)
            r.raise_for_status()
            return {"node": node, **r.json()}
        except (httpx.HTTPError, ValueError) as e:
            return {"node": node, "error": f"node-metrics on {node} did not answer: {e}"}

    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        return {"nodes": list(pool.map(read, names))}


def claims() -> Dict[str, Any]:
    """Every volume claim with its size and, where the kubelet measures it,
    the space it uses."""
    core = _core()
    used: Dict[str, int] = {}
    errors = []
    for node in core.list_node().items:
        try:
            raw = core.connect_get_node_proxy_with_path(node.metadata.name, "stats/summary",
                                                        _preload_content=False)
            summary = json.loads(raw.data)
        except (ApiException, ValueError) as e:
            errors.append(f"{node.metadata.name}: kubelet stats not read: {e}")
            continue
        for pod in summary.get("pods", []):
            for vol in pod.get("volume", []) or []:
                ref = vol.get("pvcRef")
                if ref and "usedBytes" in vol:
                    key = f"{ref['namespace']}/{ref['name']}"
                    used[key] = max(used.get(key, 0), vol["usedBytes"])
    out = []
    for pvc in core.list_persistent_volume_claim_for_all_namespaces().items:
        key = f"{pvc.metadata.namespace}/{pvc.metadata.name}"
        out.append({
            "namespace": pvc.metadata.namespace,
            "name": pvc.metadata.name,
            "storage_class": pvc.spec.storage_class_name,
            "volume": pvc.spec.volume_name,
            "phase": pvc.status.phase,
            "requested_bytes": _quantity((pvc.spec.resources.requests or {}).get("storage")),
            "capacity_bytes": _quantity((pvc.status.capacity or {}).get("storage")),
            "used_bytes": used.get(key),
        })
    out.sort(key=lambda c: (c["namespace"], c["name"]))
    return {"claims": out, "errors": errors}
