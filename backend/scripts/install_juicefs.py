# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Installs the juicefs tool of the JuiceFS release the platform's CSI driver
mounts with, for the image's architecture, checked against the release's
checksums."""

import hashlib
import io
import os
import platform
import tarfile
import urllib.request

VERSION = "1.3.0"
SHA256 = {
    "amd64": "53cbdc9e6736e576108aadeb5d10cccba4bbb44c50b0d48e0497a3f25d567153",
    "arm64": "11e29017b1c8c41966eb56feb182b75cece7a6ede764b6147e911655190c8320",
}
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
TARGET = "/usr/local/bin/juicefs"

url = (f"https://github.com/juicedata/juicefs/releases/download/v{VERSION}/"
       f"juicefs-{VERSION}-linux-{ARCH}.tar.gz")
data = urllib.request.urlopen(url, timeout=300).read()
got = hashlib.sha256(data).hexdigest()
if got != SHA256[ARCH]:
    raise SystemExit(f"{url}: sha256 {got}, expected {SHA256[ARCH]}")
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    with open(TARGET, "wb") as out:
        out.write(archive.extractfile(archive.getmember("juicefs")).read())
os.chmod(TARGET, 0o755)
