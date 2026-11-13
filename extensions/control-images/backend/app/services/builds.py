# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The workflow that builds a custom image with Buildah and pushes it to Harbor."""

import shlex
from typing import Any, Dict, List

from app.services.argo import BUILDAH_IMAGE, KEEP_SECONDS, NAMESPACE, REGISTRY_LOGIN_SECRET, SERVICE_ACCOUNT

LABEL = "thinkube.io/custom-image-build"
# Python's name of a machine, to the architecture label of a node
ARCHITECTURES = {"x86_64": "amd64", "aarch64": "arm64"}


def workflow(build_id: str, name: str, build_args: Dict[str, str], registry_url: str, architecture: str,
             artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The Argo Workflow that builds the image from /context/Dockerfile and pushes it to registry_url."""
    repository = registry_url.rsplit(":", 1)[0]
    registry_host = registry_url.split("/", 1)[0]
    arg_flags = "".join(f" --build-arg {shlex.quote(f'{k}={v}')}" for k, v in build_args.items())
    # Custom Dockerfiles name base images without a registry (FROM library/x);
    # short names resolve to Harbor only.
    script = (
        "set -e\n"
        # A node on Wi-Fi can lose DNS for seconds at a time; wait for the
        # registry's name before the first lookup that would fail the build.
        f"for try in $(seq 1 30); do getent hosts {shlex.quote(registry_host)} >/dev/null && break;"
        f" [ \"$try\" = 30 ] && {{ echo {shlex.quote(registry_host + ' does not resolve after 60s')}; exit 1; }}; sleep 2; done\n"
        f"printf 'unqualified-search-registries = [\"%s\"]\\n' {shlex.quote(registry_host)} > /tmp/registries.conf\n"
        # chroot: RUN steps run without a nested OCI runtime, which this pod
        # cannot provide. nofile: Buildah raises RLIMIT_NOFILE for RUN steps,
        # and a RUN step fails when that is above the pod's hard limit.
        "buildah build --isolation chroot --storage-driver overlay"
        " --ulimit nofile=524288:524288"
        f" --layers --cache-from {shlex.quote(repository + '/cache')} --cache-to {shlex.quote(repository + '/cache')}"
        f"{arg_flags}"
        f" -f /context/Dockerfile -t {shlex.quote(registry_url)} /context\n"
        f"buildah push --storage-driver overlay --retry 3 {shlex.quote(registry_url)}\n"
    )
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {"generateName": f"custom-image-{name}-", "namespace": NAMESPACE, "labels": {LABEL: build_id}},
        "spec": {
            "entrypoint": "build",
            "serviceAccountName": SERVICE_ACCOUNT,
            "activeDeadlineSeconds": 3 * 3600,
            "ttlStrategy": {"secondsAfterCompletion": KEEP_SECONDS},
            "volumes": [
                {"name": "docker-config", "secret": {"secretName": REGISTRY_LOGIN_SECRET}},
                {"name": "buildah-storage", "emptyDir": {}},
            ],
            "templates": [{
                "name": "build",
                "nodeSelector": {"kubernetes.io/arch": architecture},
                "inputs": {"artifacts": artifacts},
                "container": {
                    "image": f"{registry_host}/{BUILDAH_IMAGE}",
                    "command": ["/bin/bash", "-c"],
                    "args": [script],
                    # Buildah unpacks each layer in its own mount namespace, which
                    # needs root, SYS_ADMIN and no AppArmor profile that forbids
                    # those mounts; without them unpacking fails with "remount /,
                    # flags: 0x44000: permission denied".
                    "securityContext": {
                        "runAsUser": 0,
                        "capabilities": {"add": ["SYS_ADMIN"]},
                        "appArmorProfile": {"type": "Unconfined"},
                    },
                    "env": [
                        {"name": "REGISTRY_AUTH_FILE", "value": "/registry-auth/config.json"},
                        {"name": "CONTAINERS_REGISTRIES_CONF", "value": "/tmp/registries.conf"},
                    ],
                    "volumeMounts": [
                        {"name": "docker-config", "mountPath": "/registry-auth"},
                        {"name": "buildah-storage", "mountPath": "/var/lib/containers"},
                    ],
                    "resources": {
                        "requests": {"memory": "1Gi", "cpu": "500m"},
                        "limits": {"memory": "4Gi", "cpu": "4"},
                    },
                },
            }],
        },
    }
