#!/usr/bin/env python3
"""Regeneration reads what it needs or stops, and never renders a substitute.

The module is loaded from its file with fake Kubernetes clients, so these run
without a cluster and without importing the rest of the application.
"""

import base64
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
# The module adds the runtime checkout's scripts/ to the path when it loads;
# importing the repository's modules first makes it use those.
import manifest_plan  # noqa: E402,F401
import namespace_quota  # noqa: E402,F401
import thinkube_yaml_validator  # noqa: E402,F401

spec = importlib.util.spec_from_file_location(
    "manifest_generator_under_test", REPO / "backend/app/services/manifest_generator.py"
)
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)


def b64(value):
    return base64.b64encode(value.encode()).decode()


def secret(namespace, name, data):
    encoded = {key: b64(value) for key, value in data.items()}
    return SimpleNamespace(to_dict=lambda: {
        "metadata": {"namespace": namespace, "name": name},
        "data": encoded,
    })


SECRETS = {
    ("thinkube-control", "admin-credentials"): secret("thinkube-control", "admin-credentials", {
        "admin-username": "tkadmin", "admin-password": "pw"}),
    ("thinkube-control", "mlflow-auth-config"): secret("thinkube-control", "mlflow-auth-config", {
        "keycloak-token-url": "https://auth/token", "client-id": "mlflow", "client-secret": "s",
        "username": "mlflow-user", "password": "mlflow-pw"}),
    ("seaweedfs", "seaweedfs-s3-credentials"): secret("seaweedfs", "seaweedfs-s3-credentials", {
        "secret_key": "sk", "access_key": "ak", "endpoint_internal": "http://seaweedfs:8333"}),
}


class Core:
    def __init__(self, secrets=None, config_map=None, services=None, fail=None):
        self.secrets = SECRETS if secrets is None else secrets
        self.config_map = config_map
        self.services = services or []
        self.fail = fail or {}
        self.read = []

    def read_namespaced_secret(self, name, namespace):
        self.read.append((namespace, name))
        if (namespace, name) not in self.secrets:
            raise ApiException(status=404, reason="Not Found")
        return self.secrets[(namespace, name)]

    def read_namespaced_config_map(self, name, namespace):
        if self.config_map is None:
            raise ApiException(status=403, reason="Forbidden")
        return SimpleNamespace(data=self.config_map)

    def list_service_for_all_namespaces(self):
        if "services" in self.fail:
            raise ApiException(status=500, reason="Internal Server Error")
        return SimpleNamespace(items=self.services)


class Custom:
    def __init__(self, items=None, status=None):
        self.items = items or []
        self.status = status

    def list_cluster_custom_object(self, group, version, plural):
        if self.status:
            raise ApiException(status=self.status, reason="error")
        return {"items": self.items}


def generator(core=None, custom=None):
    g = mg.ManifestGenerator(app_name="wf-check", domain="thinkube.com")
    g._core_v1 = core or Core()
    g._custom_objects = custom or Custom()
    return g


def service(name, namespace, ports=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        spec=SimpleNamespace(ports=[SimpleNamespace(port=p) for p in (ports or [])]),
    )


# --- credentials --------------------------------------------------------------


def test_mlflow_credentials_come_from_the_secret_the_first_deploy_reads():
    g = generator()
    g._fetch_secrets()
    assert ("thinkube-control", "mlflow-auth-config") in g.core_v1.read
    assert g.secrets["mlflow_keycloak_client_id"] == "mlflow"
    assert g.secrets["mlflow_password"] == "mlflow-pw"


def test_the_admin_username_comes_from_its_secret():
    g = generator()
    g._fetch_secrets()
    assert g.secrets["admin_username"] == "tkadmin"


@pytest.mark.parametrize("missing", list(SECRETS))
def test_a_missing_credentials_secret_stops_regeneration(missing):
    secrets = {k: v for k, v in SECRETS.items() if k != missing}
    g = generator(Core(secrets=secrets))
    with pytest.raises(RuntimeError, match=missing[1]):
        g._fetch_secrets()


def test_a_secret_without_the_key_stops_instead_of_rendering_empty():
    secrets = dict(SECRETS)
    secrets[("thinkube-control", "mlflow-auth-config")] = secret(
        "thinkube-control", "mlflow-auth-config", {"keycloak-token-url": "x"})
    with pytest.raises(RuntimeError, match="has no value for 'client-id'"):
        generator(Core(secrets=secrets))._fetch_secrets()


# --- template parameters ------------------------------------------------------


def test_parameters_are_read_from_the_app_metadata_config_map():
    g = generator(Core(config_map={"app_name": "wf-check", "containers": "[]", "model_id": "m"}))
    assert g._read_manifest_params() == {"model_id": "m"}


def test_an_unreadable_config_map_stops_rather_than_dropping_parameters():
    with pytest.raises(RuntimeError, match="wf-check-metadata"):
        generator(Core(config_map=None))._read_manifest_params()


# --- dependencies -------------------------------------------------------------


def test_without_knative_no_knative_service_can_match():
    assert generator(custom=Custom(status=404))._find_knative_service_url("aligner") is None


def test_a_failing_knative_listing_is_reported():
    with pytest.raises(RuntimeError, match="Listing Knative services failed"):
        generator(custom=Custom(status=500))._find_knative_service_url("aligner")


def test_a_knative_service_without_an_address_is_not_ready():
    item = {"metadata": {"name": "aligner", "namespace": "aligner"}, "status": {}}
    with pytest.raises(RuntimeError, match="not ready"):
        generator(custom=Custom(items=[item]))._find_knative_service_url("aligner")


def test_a_ready_knative_service_resolves_to_its_address():
    item = {"metadata": {"name": "aligner", "namespace": "aligner"},
            "status": {"address": {"url": "http://aligner.aligner.svc.cluster.local"}}}
    assert generator(custom=Custom(items=[item]))._find_knative_service_url("aligner") == \
        "http://aligner.aligner.svc.cluster.local"


def test_a_failing_service_listing_is_reported():
    with pytest.raises(RuntimeError, match="Listing services failed"):
        generator(Core(fail={"services": True}))._find_k8s_service_url("ollama")


def test_a_matching_service_without_a_port_is_refused():
    with pytest.raises(RuntimeError, match="exposes no port"):
        generator(Core(services=[service("ollama", "ollama")]))._find_k8s_service_url("ollama")


def test_a_matching_service_resolves_to_its_first_port():
    core = Core(services=[service("ollama", "ollama", ports=[11434])])
    assert generator(core)._find_k8s_service_url("ollama") == "http://ollama.ollama.svc.cluster.local:11434"


def test_a_dependency_without_a_type_is_refused():
    g = generator(Core(services=[service("anything", "anywhere", ports=[80])]))
    g.thinkube_config = {"spec": {"dependencies": [{"name": "splitter", "env": "SPLITTER_URL"}]}}
    with pytest.raises(ValueError, match="has no type"):
        g._resolve_dependencies()


# --- the identity that writes <app>-secrets -----------------------------------


def test_writing_the_app_secret_needs_the_deploy_kubeconfig(tmp_path, monkeypatch):
    monkeypatch.setattr(mg, "DEPLOY_KUBECONFIG", tmp_path / "missing" / "config")
    with pytest.raises(RuntimeError, match="kubeconfig the first deploy uses"):
        mg._deploy_core_client()
