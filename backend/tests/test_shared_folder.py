# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The folder of a volume claim is found from the claim's PV and never
leaves the mounted file system."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.services import shared_folder as sf

FS = {"name": "thinkube-shared", "secret": "juicefs/juicefs-secret", "meta": "postgres://u@h/db",
      "password": "p", "mount": Path("/storage/thinkube-shared")}


def test_quantities():
    assert sf._quantity("200Gi") == 200 * sf.GIB
    assert sf._quantity("1Ti") == 1024 * sf.GIB
    with pytest.raises(sf.FolderError):
        sf._quantity("lots")


def test_paths_never_leave_the_file_system():
    with pytest.raises(sf.FolderError):
        sf._inside(FS, "pvc-models/../../etc")
    assert sf._inside(FS, "/pvc-models/run1/") == Path("/storage/thinkube-shared/pvc-models/run1")


def _volume(name, claim, sub_path):
    namespace, claim_name = claim.split("/")
    return NS(
        metadata=NS(name=name),
        spec=NS(
            csi=NS(driver=sf.JUICEFS_DRIVER, node_publish_secret_ref=NS(namespace="juicefs", name="juicefs-secret"),
                   volume_attributes={"subPath": sub_path, "capacity": "100Gi"}),
            claim_ref=NS(namespace=namespace, name=claim_name),
            capacity={"storage": "100Gi"},
        ),
    )


def test_a_claims_folder_is_the_sub_path_of_its_volume(monkeypatch, tmp_path):
    fs = {**FS, "mount": tmp_path}
    (tmp_path / "pvc-notebooks").mkdir()
    core = NS(list_persistent_volume=lambda: NS(items=[
        _volume("pvc-notebooks", "jupyterhub/jupyterhub-notebooks-pvc", "pvc-notebooks"),
        _volume("pvc-models", "jupyterhub/models", "pvc-models"),
    ]))
    monkeypatch.setattr(sf, "_core", lambda: core)
    monkeypatch.setattr(sf, "_filesystems", lambda c: {"thinkube-shared": fs})
    assert sf.claim_folder("jupyterhub/jupyterhub-notebooks-pvc") == tmp_path / "pvc-notebooks"
    with pytest.raises(sf.FolderError, match="is not mounted here"):
        sf.claim_folder("jupyterhub/models")
    with pytest.raises(sf.FolderError, match="mounts 0 JuiceFS folders"):
        sf.claim_folder("jupyterhub/none")
