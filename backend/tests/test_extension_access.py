#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""containers[].access and containers[].filesystems of an extension.

These render the real templates in templates/k8s/ with the variables the
generator passes for an extension, so what is asserted here is what lands in
the extension's k8s/.
"""

import json
import sys
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"
sys.path.insert(0, str(REPO / "scripts"))

from manifest_plan import (  # noqa: E402
    EXTENSION_ACCESS_MANIFEST,
    EXTENSION_FILESYSTEMS,
    EXTENSION_NAMESPACE,
    artifact_repository_name,
    has_extension_access,
    home_folder_vars,
    kustomization_resources,
)
from platform_credentials import artifact_secret_name  # noqa: E402
from thinkube_yaml_validator import validate_extension_access  # noqa: E402

EXTENSION = "storage"
RULES = [
    {"apiGroups": [""], "resources": ["persistentvolumes"], "verbs": ["get", "list", "patch"]},
    {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"], "resourceNames": ["juicefs-secret"]},
]


def spec(backend=None, services=None, deployment_type="extension"):
    return {
        "apiVersion": "thinkube.io/v1",
        "kind": "ThinkubeDeployment",
        "metadata": {"name": EXTENSION},
        "spec": {
            "deployment": {"type": deployment_type, "replicas": 1},
            "containers": [
                {"name": "backend", "build": "./backend", "port": 8000, "health": "/health", **(backend or {})},
                {"name": "frontend", "build": "./frontend", "port": 80, "health": "/health"},
            ],
            **({"services": services} if services is not None else {}),
        },
    }


def render(name, config):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    environment.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    environment.filters["to_json"] = lambda x: json.dumps(x)
    docs = environment.get_template(name).render(
        project_name=EXTENSION,
        k8s_namespace=EXTENSION_NAMESPACE,
        domain_name="thinkube.com",
        container_registry="registry.thinkube.com",
        admin_username="tkadmin",
        thinkube_spec=config,
        manifest_params={},
        spec_env={},
        seaweedfs_endpoint="http://seaweedfs-filer.seaweedfs.svc.cluster.local:8333",
        deployment_env_from=[],
        extension=True,
        artifact_repository=artifact_repository_name(EXTENSION, True),
        artifact_secret=artifact_secret_name(EXTENSION, True),
        extension_filesystems=EXTENSION_FILESYSTEMS,
        **home_folder_vars({"SYSTEM_USERNAME": "tkuser", "MASTER_NODE_NAME": "node1"}),
    )
    return [d for d in yaml.safe_load_all(docs) if d]


def by_kind(docs, kind):
    return [d for d in docs if d.get("kind") == kind]


def pod(docs, container):
    deployment = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == f"{EXTENSION}-{container}")
    return deployment["spec"]["template"]["spec"]


# --- the validator -----------------------------------------------------------


def test_an_extension_may_declare_access_and_filesystems():
    assert validate_extension_access(spec({"access": RULES, "filesystems": ["thinkube-shared", "mlflow"]})) == []


def test_an_app_may_declare_neither():
    errors = validate_extension_access(spec({"access": RULES, "filesystems": ["mlflow"]}, deployment_type="app"))
    assert len(errors) == 2
    assert all("extension only" in e for e in errors)


def test_a_rule_names_its_groups_resources_and_verbs():
    for rule in (
        {"apiGroups": [""], "resources": ["pods"]},
        {"apiGroups": [""], "resources": [], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["*"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["*"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get"], "namespaces": ["juicefs"]},
        "pods",
    ):
        assert validate_extension_access(spec({"access": [rule]})), rule
    assert validate_extension_access(spec({"access": []}))


def test_a_file_system_is_one_of_the_platforms_and_named_once():
    assert validate_extension_access(spec({"filesystems": ["scratch"]}))
    assert validate_extension_access(spec({"filesystems": ["mlflow", "mlflow"]}))
    assert validate_extension_access(spec({"filesystems": []}))


def test_filesystems_are_not_combined_with_a_volume_or_a_gpu():
    assert validate_extension_access(spec({"filesystems": ["mlflow"], "volume": "/data"}))
    assert validate_extension_access(spec({"filesystems": ["mlflow"], "gpu": {"count": 1, "memory": "8Gi"}}))


# --- the plan ----------------------------------------------------------------


def test_the_manifest_is_listed_only_when_a_container_declares_one_of_them():
    assert not has_extension_access(spec())
    assert has_extension_access(spec({"access": RULES}))
    assert has_extension_access(spec({"filesystems": ["mlflow"]}))
    assert not has_extension_access(spec({"access": RULES}, deployment_type="app"))
    listed = kustomization_resources(
        is_knative=False, needs_storage=False, has_workflows=False, extension=True, has_access=True)
    assert EXTENSION_ACCESS_MANIFEST in listed
    assert EXTENSION_ACCESS_MANIFEST not in kustomization_resources(
        is_knative=False, needs_storage=False, has_workflows=False, extension=True)


# --- extension-access.yaml ---------------------------------------------------


def test_access_makes_an_account_a_cluster_role_and_its_binding():
    docs = render("extension-access.j2", spec({"access": RULES}))
    account = by_kind(docs, "ServiceAccount")[0]
    assert account["metadata"]["name"] == "storage-backend"
    assert account["metadata"]["namespace"] == "thinkube-control"
    assert account["metadata"]["labels"]["app.kubernetes.io/name"] == EXTENSION
    role = by_kind(docs, "ClusterRole")[0]
    assert role["metadata"]["name"] == "thinkube-extension-storage-backend"
    assert role["metadata"]["labels"]["thinkube.io/extension"] == EXTENSION
    assert role["rules"] == RULES
    binding = by_kind(docs, "ClusterRoleBinding")[0]
    assert binding["metadata"]["labels"]["thinkube.io/extension"] == EXTENSION
    assert binding["roleRef"]["name"] == "thinkube-extension-storage-backend"
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "storage-backend", "namespace": "thinkube-control"}
    ]
    assert by_kind(docs, "RoleBinding") == []


def test_with_workflows_the_account_also_gets_the_workflows_role():
    docs = render("extension-access.j2", spec({"access": RULES}, services=["workflows"]))
    binding = by_kind(docs, "RoleBinding")[0]
    assert binding["roleRef"]["name"] == "storage-workflows"
    assert binding["subjects"][0]["name"] == "storage-backend"


def test_a_file_system_is_a_static_volume_of_its_root_and_a_claim():
    docs = render("extension-access.j2", spec({"filesystems": ["thinkube-shared", "mlflow"]}))
    volumes = {v["metadata"]["name"]: v for v in by_kind(docs, "PersistentVolume")}
    assert set(volumes) == {"storage-backend-storage-thinkube-shared", "storage-backend-storage-mlflow"}
    shared = volumes["storage-backend-storage-thinkube-shared"]
    assert shared["metadata"]["labels"]["thinkube.io/extension"] == EXTENSION
    assert shared["spec"]["csi"]["nodePublishSecretRef"] == {"name": "juicefs-secret", "namespace": "juicefs"}
    assert shared["spec"]["csi"]["volumeHandle"] == "storage-backend-storage-thinkube-shared"
    assert "volumeAttributes" not in shared["spec"]["csi"]
    assert shared["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
    assert volumes["storage-backend-storage-mlflow"]["spec"]["csi"]["nodePublishSecretRef"]["name"] == \
        "juicefs-mlflow-secret"
    claims = {c["metadata"]["name"]: c for c in by_kind(docs, "PersistentVolumeClaim")}
    assert claims["storage-backend-storage-mlflow"]["spec"]["volumeName"] == "storage-backend-storage-mlflow"
    assert claims["storage-backend-storage-mlflow"]["metadata"]["labels"]["app.kubernetes.io/name"] == EXTENSION
    assert by_kind(docs, "ServiceAccount") == []


# --- deployments.yaml --------------------------------------------------------


def test_only_the_container_that_declares_access_runs_as_its_account():
    docs = render("deployment-separate.j2", spec({"access": RULES}, services=["workflows"]))
    assert pod(docs, "backend")["serviceAccountName"] == "storage-backend"
    assert pod(docs, "frontend")["serviceAccountName"] == "storage-workflows"
    docs = render("deployment-separate.j2", spec({"access": RULES}))
    assert "serviceAccountName" not in pod(docs, "frontend")


def test_only_the_container_that_declares_filesystems_mounts_them():
    docs = render("deployment-separate.j2", spec({"filesystems": ["thinkube-shared", "mlflow"]}))
    backend = pod(docs, "backend")
    assert backend["containers"][0]["volumeMounts"] == [
        {"name": "storage-thinkube-shared", "mountPath": "/storage/thinkube-shared"},
        {"name": "storage-mlflow", "mountPath": "/storage/mlflow"},
    ]
    assert backend["volumes"] == [
        {"name": "storage-thinkube-shared",
         "persistentVolumeClaim": {"claimName": "storage-backend-storage-thinkube-shared"}},
        {"name": "storage-mlflow", "persistentVolumeClaim": {"claimName": "storage-backend-storage-mlflow"}},
    ]
    frontend = pod(docs, "frontend")
    assert "volumes" not in frontend
    assert "volumeMounts" not in frontend["containers"][0]


# --- a folder of the IDE's home ----------------------------------------------


def test_a_home_folder_is_a_file_system_an_extension_may_declare():
    assert validate_extension_access(spec({"filesystems": ["dockerfiles"]})) == []


def test_a_home_folder_is_a_volume_of_the_pod_on_the_node_that_holds_the_home():
    docs = render("deployment-separate.j2", spec({"filesystems": ["dockerfiles", "mlflow"]}))
    backend = pod(docs, "backend")
    assert backend["nodeSelector"] == {"kubernetes.io/hostname": "node1"}
    assert backend["containers"][0]["volumeMounts"] == [
        {"name": "storage-dockerfiles", "mountPath": "/storage/dockerfiles"},
        {"name": "storage-mlflow", "mountPath": "/storage/mlflow"},
    ]
    assert backend["volumes"] == [
        {"name": "storage-dockerfiles",
         "hostPath": {"path": "/home/tkuser/shared-code/dockerfiles", "type": "DirectoryOrCreate"}},
        {"name": "storage-mlflow", "persistentVolumeClaim": {"claimName": "storage-backend-storage-mlflow"}},
    ]
    assert "nodeSelector" not in pod(docs, "frontend")


def test_a_home_folder_has_no_volume_object_or_claim():
    docs = render("extension-access.j2", spec({"filesystems": ["dockerfiles"]}))
    assert by_kind(docs, "PersistentVolume") == []
    assert by_kind(docs, "PersistentVolumeClaim") == []


def test_a_shared_file_system_alone_does_not_bind_the_pod_to_a_node():
    docs = render("deployment-separate.j2", spec({"filesystems": ["mlflow"]}))
    assert "nodeSelector" not in pod(docs, "backend")


def test_the_homes_place_comes_from_controls_environment():
    try:
        home_folder_vars({"SYSTEM_USERNAME": "tkuser"})
    except RuntimeError as e:
        assert "MASTER_NODE_NAME not set" in str(e)
    else:
        raise AssertionError("a missing MASTER_NODE_NAME was accepted")
