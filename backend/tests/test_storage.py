# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Storage reads quotas and sizes as juicefs prints them, sets a quota on
the folder and on the PV the driver reads it from, and refuses what would
break a volume or overfill the disk."""

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from app.services import storage as st

QUOTA_LIST = """\
+-------------------------------------------+---------+---------+------+-----------+-------+-------+
|                    Path                   |   Size  |   Used  | Use% |   Inodes  | IUsed | IUse% |
+-------------------------------------------+---------+---------+------+-----------+-------+-------+
| /pvc-models | 600 GiB | 200 GiB |  33% | unlimited |   259 |       |
| /pvc-notebooks | 100 GiB | 7.0 GiB |   7% | unlimited |   531 |       |
+-------------------------------------------+---------+---------+------+-----------+-------+-------+
"""

FS = {"name": "thinkube-shared", "secret": "juicefs/juicefs-secret", "meta": "postgres://u@h/db",
      "password": "p", "mount": Path("/storage/thinkube-shared")}


def test_quotas_are_read_from_the_table(monkeypatch):
    monkeypatch.setattr(st, "_juicefs", lambda fs, *a, **k: QUOTA_LIST)
    assert st._quotas(FS) == {"pvc-models": {"quota_bytes": 600 * st.GIB},
                              "pvc-notebooks": {"quota_bytes": 100 * st.GIB}}


def test_sizes_and_quantities():
    assert st._size("7.0 GiB") == 7 * st.GIB
    assert st._quantity("200Gi") == 200 * st.GIB
    assert st._quantity("1Ti") == 1024 * st.GIB
    with pytest.raises(st.StorageError):
        st._size("lots")


def test_paths_never_leave_the_file_system():
    with pytest.raises(st.StorageError):
        st._inside(FS, "pvc-models/../../etc")
    assert st._inside(FS, "/pvc-models/run1/") == Path("/storage/thinkube-shared/pvc-models/run1")


def test_a_top_level_folder_is_not_deleted(monkeypatch):
    monkeypatch.setattr(st, "_core", MagicMock)
    monkeypatch.setattr(st, "_fs", lambda core, name: FS)
    with pytest.raises(st.StorageError, match="top-level folder"):
        st.delete("thinkube-shared", "pvc-models")
    with pytest.raises(st.StorageError, match="internal folder"):
        st.delete("thinkube-shared", ".trash/x")


def _quota_setup(monkeypatch, used, free):
    core = MagicMock()
    monkeypatch.setattr(st, "_core", lambda: core)
    monkeypatch.setattr(st, "_fs", lambda c, name: FS)
    calls = []

    def juicefs(fs, *args, **kw):
        calls.append(args)
        return QUOTA_LIST
    monkeypatch.setattr(st, "_juicefs", juicefs)
    monkeypatch.setattr(st, "_summary", lambda fs, rel: {"bytes": used, "dirs": 0, "files": 0, "entries": []})
    monkeypatch.setattr(st, "_disk_under", lambda c: [{"free_bytes": free}])
    monkeypatch.setattr(st, "_juicefs_volumes", lambda c, f: {
        ("thinkube-shared", "pvc-models"): [
            {"volume": "pvc-models", "claim": "jupyterhub/models", "capacity_bytes": 0, "sets_quota": True},
            {"volume": "code-server-models-pv", "claim": "code-server/models", "capacity_bytes": 0,
             "sets_quota": False},
        ]})
    return core, calls


def test_a_quota_is_set_on_the_folder_and_on_the_pv_the_driver_reads(monkeypatch):
    core, calls = _quota_setup(monkeypatch, used=200 * st.GIB, free=900 * st.GIB)

    result = st.set_quota("thinkube-shared", "pvc-models", 600)

    core.patch_persistent_volume.assert_called_once_with(
        "pvc-models", {"spec": {"capacity": {"storage": "600Gi"}}})
    assert ("quota", "set", st.META, "--path", "/pvc-models", "--capacity", "600") in calls
    assert result["volumes_patched"] == ["pvc-models"]


def test_a_quota_below_the_content_or_beyond_the_disk_is_refused(monkeypatch):
    core, _ = _quota_setup(monkeypatch, used=200 * st.GIB, free=100 * st.GIB)
    with pytest.raises(st.StorageError, match="more than 150 GiB"):
        st.set_quota("thinkube-shared", "pvc-models", 150)
    with pytest.raises(st.StorageError, match="disks under it have 100 GiB free"):
        st.set_quota("thinkube-shared", "pvc-models", 400)
    core.patch_persistent_volume.assert_not_called()


def test_a_folder_without_a_quota_gets_none(monkeypatch):
    _quota_setup(monkeypatch, used=0, free=900 * st.GIB)
    with pytest.raises(st.StorageError, match="has no quota"):
        st.set_quota("thinkube-shared", "mlflow-root", 10)
