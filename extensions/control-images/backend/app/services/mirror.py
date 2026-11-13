# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The workflow that mirrors one image into Harbor.

The step reads the source's manifest list, keeps the Linux images of the
cluster's architectures, and pushes those to the destination as one manifest
list. Layers go from the source registry to Harbor directly; nothing is
stored in the pod.
"""

import json
from typing import Any, Dict, List, Optional

from app.services.argo import BUILDAH_IMAGE, KEEP_SECONDS, NAMESPACE, REGISTRY_LOGIN_SECRET, SERVICE_ACCOUNT

LABEL = "thinkube.io/image-mirror-job"
RESULT_MARK = "MIRRORED "

# Runs in the Buildah image, which has Python. vfs: the step stores manifests
# only, and vfs needs no privilege.
STEP = r'''
import json, subprocess, sys

source, destination, wanted = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
BUILDAH = ["buildah", "--storage-driver", "vfs"]


def run(*args):
    print("+ buildah " + " ".join(args), flush=True)
    done = subprocess.run([*BUILDAH, *args], text=True, capture_output=True)
    if done.stderr.strip():
        print(done.stderr.strip(), flush=True)
    if done.returncode != 0:
        sys.exit(f"buildah {args[0]} {args[1]} failed with code {done.returncode}")
    return done.stdout


run("manifest", "create", "--all", "mirror", f"docker://{source}")
listed = json.loads(run("manifest", "inspect", "mirror"))["manifests"]
linux = [m for m in listed if (m.get("platform") or {}).get("os") == "linux"]
provided = sorted({m["platform"]["architecture"] for m in linux})
keep = [m for m in linux if m["platform"]["architecture"] in wanted]
if not keep:
    sys.exit(f"{source} provides {provided}; the cluster's nodes are {wanted}")
for manifest in listed:
    if manifest not in keep:
        run("manifest", "remove", "mirror", manifest["digest"])
print(f"Mirroring {source} to {destination} for {sorted({m['platform']['architecture'] for m in keep})}", flush=True)
run("manifest", "push", "--all", "--digestfile", "/tmp/digest", "mirror", f"docker://{destination}")
digest = open("/tmp/digest").read().strip()
print("MIRRORED " + json.dumps({"digest": digest,
                                "architectures": sorted({m["platform"]["architecture"] for m in keep})}), flush=True)
'''


def workflow(job_id: str, name: str, source: str, destination: str, architectures: List[str]) -> Dict[str, Any]:
    """The Argo Workflow that mirrors source to destination for these architectures."""
    registry_host = destination.split("/", 1)[0]
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {"generateName": f"image-mirror-{name}-", "namespace": NAMESPACE, "labels": {LABEL: job_id}},
        "spec": {
            "entrypoint": "mirror",
            "serviceAccountName": SERVICE_ACCOUNT,
            # A 30 GB image for two architectures takes well over ten minutes
            "activeDeadlineSeconds": 6 * 3600,
            "ttlStrategy": {"secondsAfterCompletion": KEEP_SECONDS},
            "volumes": [{"name": "registry-login", "secret": {"secretName": REGISTRY_LOGIN_SECRET}}],
            "templates": [{
                "name": "mirror",
                "container": {
                    "image": f"{registry_host}/{BUILDAH_IMAGE}",
                    "command": ["python3", "-c", STEP, source, destination, ",".join(architectures)],
                    "env": [{"name": "REGISTRY_AUTH_FILE", "value": "/registry-login/config.json"}],
                    "volumeMounts": [{"name": "registry-login", "mountPath": "/registry-login"}],
                    "resources": {
                        "requests": {"memory": "256Mi", "cpu": "250m"},
                        "limits": {"memory": "2Gi", "cpu": "2"},
                    },
                },
            }],
        },
    }


def workflow_name_part(image_name: str) -> str:
    """The image's name as part of a Kubernetes object name."""
    cleaned = "".join(c if c.isalnum() else "-" for c in image_name.lower()).strip("-")
    return cleaned[:40] or "image"


def result(log: str) -> Optional[Dict[str, Any]]:
    """What the step printed when it ended well: the digest pushed and its architectures."""
    for line in reversed(log.splitlines()):
        if line.startswith(RESULT_MARK):
            return json.loads(line[len(RESULT_MARK):])
    return None
