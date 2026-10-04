#!/usr/bin/env python3

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Secrets an application declares, and what it receives from the store.

The helper is the one both deploy paths use. The rendering cases render the
real templates in templates/k8s/ with the variable the generator passes, so
what is asserted is what lands in k8s/.
"""

import json
import sys
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates/k8s"
sys.path.insert(0, str(REPO / "scripts"))

from app_secrets import (  # noqa: E402
    DeclaredSecret,
    SecretsRefused,
    declared_secrets,
    env_from,
    missing_required,
    public_env_conflicts,
    refusal,
    secret_data,
    secret_manifest,
    secret_resource_name,
)

APP = "wf-check"
DOMAIN = "thinkube.com"


def manifest(*entries):
    return {"apiVersion": "thinkube.io/v1", "kind": "TemplateManifest", "secrets": list(entries)}


class Store:
    """A Secrets store that records every name it is asked about."""

    def __init__(self, values):
        self.values = dict(values)
        self.asked = []

    def lookup(self, name):
        self.asked.append(name)
        return self.values.get(name)


# --- pull by declaration ------------------------------------------------------


def test_a_store_of_ten_delivers_only_the_two_declared():
    store = Store({f"SECRET_{i}": f"value-{i}" for i in range(10)})
    declared = declared_secrets(manifest({"name": "SECRET_3"}, {"name": "SECRET_7"}))

    data = secret_data(declared, store.lookup)

    assert data == {"SECRET_3": "value-3", "SECRET_7": "value-7"}


def test_the_store_is_asked_about_declared_names_and_no_others():
    store = Store({f"SECRET_{i}": f"value-{i}" for i in range(10)})
    declared = declared_secrets(manifest({"name": "SECRET_3"}, {"name": "SECRET_7"}))

    secret_data(declared, store.lookup)

    assert sorted(store.asked) == ["SECRET_3", "SECRET_7"]


def test_an_optional_secret_that_is_absent_sets_no_key_and_does_not_fail():
    store = Store({"WF_CHECK_TOKEN": "abc"})
    declared = declared_secrets(
        manifest({"name": "WF_CHECK_TOKEN"}, {"name": "WF_CHECK_OPTIONAL", "required": False})
    )

    assert secret_data(declared, store.lookup) == {"WF_CHECK_TOKEN": "abc"}
    assert missing_required(APP, declared, store.values) == []
    assert env_from(APP, declared) == [{"secretRef": {"name": "wf-check-secrets"}}]


# --- refusals ----------------------------------------------------------------


def test_a_required_secret_missing_from_the_store_fails_with_the_exact_message():
    declared = declared_secrets(manifest({"name": "WF_CHECK_TOKEN"}))
    assert missing_required(APP, declared, present=[]) == [
        "Secret 'WF_CHECK_TOKEN' is required by wf-check and is not in the Secrets store"
    ]


def test_the_refusal_points_at_the_secrets_page():
    declared = declared_secrets(manifest({"name": "WF_CHECK_TOKEN"}))
    problems = refusal(APP, DOMAIN, {"spec": {}}, declared, present=[])
    assert "Secret 'WF_CHECK_TOKEN' is required by wf-check and is not in the Secrets store" in problems
    assert "Add it on the Secrets page: https://control.thinkube.com/secrets" in problems


def test_required_defaults_to_true():
    [secret] = declared_secrets(manifest({"name": "HF_TOKEN"}))
    assert secret.required is True


def test_public_env_naming_a_secret_is_refused_naming_both():
    declared = declared_secrets(manifest({"name": "HF_TOKEN"}))
    config = {"spec": {"containers": [
        {"name": "frontend", "publicEnv": ["APP_TITLE", "HF_TOKEN"]},
    ]}}
    [conflict] = public_env_conflicts(config, declared)
    assert "'frontend'" in conflict
    assert "'HF_TOKEN'" in conflict
    assert "publicEnv" in conflict


def test_public_env_without_secrets_is_left_alone():
    declared = declared_secrets(manifest({"name": "HF_TOKEN"}))
    config = {"spec": {"containers": [{"name": "frontend", "publicEnv": ["APP_TITLE"]}]}}
    assert public_env_conflicts(config, declared) == []


def test_nothing_stops_delivery_when_everything_is_present():
    declared = declared_secrets(manifest({"name": "WF_CHECK_TOKEN"}))
    assert refusal(APP, DOMAIN, {"spec": {}}, declared, present=["WF_CHECK_TOKEN"]) == []


@pytest.mark.parametrize("bad", ["hf_token", "1TOKEN", "HF-TOKEN", "HF TOKEN", ""])
def test_a_name_that_is_not_an_environment_variable_is_refused(bad):
    with pytest.raises(SecretsRefused):
        declared_secrets(manifest({"name": bad}))


def test_a_name_declared_twice_is_refused():
    with pytest.raises(SecretsRefused, match="more than once"):
        declared_secrets(manifest({"name": "HF_TOKEN"}, {"name": "HF_TOKEN"}))


def test_required_must_be_a_boolean():
    with pytest.raises(SecretsRefused, match="true or false"):
        declared_secrets(manifest({"name": "HF_TOKEN", "required": "no"}))


def test_the_refusal_is_a_value_error_so_regeneration_answers_400():
    assert issubclass(SecretsRefused, ValueError)


# --- nothing declared ----------------------------------------------------------


@pytest.mark.parametrize(
    "m", [None, {"secrets": None}, {"secrets": "HF_TOKEN"}, "secrets: []"],
    ids=["empty-manifest", "null-secrets", "not-a-list", "not-a-mapping"],
)
def test_a_manifest_that_is_not_a_valid_declaration_is_refused(m):
    """Nothing is read as "no secrets" unless the manifest says so."""
    with pytest.raises(SecretsRefused):
        declared_secrets(m)


def test_a_description_that_is_not_text_is_refused():
    with pytest.raises(SecretsRefused, match="description must be text"):
        declared_secrets(manifest({"name": "HF_TOKEN", "description": 42}))


@pytest.mark.parametrize(
    "m", [{}, manifest(), {"parameters": []}],
    ids=["no-secrets-key", "empty-list", "parameters-only"],
)
def test_nothing_declared_means_no_secret_and_no_env_from(m):
    declared = declared_secrets(m)
    assert declared == []
    assert env_from(APP, declared) == []
    assert refusal(APP, DOMAIN, {"spec": {}}, declared, present=[]) == []


def test_the_secret_is_named_for_the_application():
    assert secret_resource_name("wf-check") == "wf-check-secrets"


# --- the templates -----------------------------------------------------------


def jinja():
    """The same environment the generator builds, filters included."""
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        lstrip_blocks=True,
        trim_blocks=True,
    )
    environment.filters["to_yaml"] = lambda x: yaml.dump(x, default_flow_style=False)
    environment.filters["to_json"] = lambda x: json.dumps(x)
    return environment


def base_vars(spec, **extra):
    values = {
        "project_name": APP,
        "k8s_namespace": APP,
        "domain_name": DOMAIN,
        "container_registry": "registry.thinkube.com",
        "admin_username": "tkadmin",
        "admin_password": "x",
        "thinkube_spec": spec,
        "manifest_params": {},
        "mlflow_keycloak_token_url": "",
        "mlflow_keycloak_client_id": "",
        "mlflow_client_secret": "",
        "mlflow_username": "",
        "mlflow_password": "",
        "seaweedfs_password": "",
        "seaweedfs_access_key": "",
        "seaweedfs_endpoint": "",
    }
    values.update(extra)
    return values


APP_SPEC = {"spec": {
    "deployment": {"type": "app", "replicas": 1},
    "containers": [
        {"name": "backend", "build": "./backend", "port": 8000, "health": "/health"},
        {"name": "frontend", "build": "./frontend", "port": 80, "health": "/health"},
    ],
    "services": ["database"],
}}

KNATIVE_SPEC = {"spec": {
    "deployment": {"type": "knative"},
    "containers": [{"name": "aligner", "build": ".", "port": 8080, "health": "/health"}],
}}

DECLARED = [DeclaredSecret("WF_CHECK_TOKEN")]


def containers_of(rendered):
    out = []
    for doc in yaml.safe_load_all(rendered):
        if not doc:
            continue
        pod = doc["spec"]["template"]["spec"]
        out.extend(pod["containers"])
    return out


def test_every_app_container_reads_the_secret_with_env_from():
    rendered = jinja().get_template("deployment-separate.j2").render(
        **base_vars(APP_SPEC, deployment_env_from=env_from(APP, DECLARED))
    )
    containers = containers_of(rendered)
    assert len(containers) == 2
    for c in containers:
        assert c["envFrom"] == [{"secretRef": {"name": "wf-check-secrets"}}], c["name"]


def test_the_knative_container_reads_the_secret_with_env_from():
    rendered = jinja().get_template("knative-service.j2").render(
        **base_vars(KNATIVE_SPEC, deployment_env_from=env_from(APP, DECLARED))
    )
    [container] = containers_of(rendered)
    assert container["envFrom"] == [{"secretRef": {"name": "wf-check-secrets"}}]


def test_no_value_reaches_the_rendered_manifest():
    """k8s/ is committed to git: only the Secret's name may appear there."""
    rendered = jinja().get_template("deployment-separate.j2").render(
        **base_vars(APP_SPEC, deployment_env_from=env_from(APP, DECLARED))
    )
    assert "WF_CHECK_TOKEN" not in rendered


@pytest.mark.parametrize("template,spec", [
    ("deployment-separate.j2", APP_SPEC),
    ("knative-service.j2", KNATIVE_SPEC),
])
def test_without_secrets_the_manifest_is_exactly_what_it_was(template, spec):
    """An empty env_from renders byte for byte what the generator produced before."""
    before = jinja().get_template(template).render(**base_vars(spec))
    after = jinja().get_template(template).render(
        **base_vars(spec, deployment_env_from=env_from(APP, []))
    )
    assert after == before
    assert "envFrom" not in after


# --- the Secret applied in the cluster ----------------------------------------


def test_the_secret_holds_only_the_declared_keys_of_a_larger_store():
    store = Store({f"SECRET_{i}": f"value-{i}" for i in range(10)})
    declared = declared_secrets(manifest({"name": "SECRET_3"}, {"name": "SECRET_7"}))

    body = secret_manifest(APP, APP, secret_data(declared, store.lookup))

    assert body["stringData"] == {"SECRET_3": "value-3", "SECRET_7": "value-7"}


def test_the_secret_is_named_and_placed_for_the_application():
    body = secret_manifest("wf-check", "wf-check", {"WF_CHECK_TOKEN": "abc"})
    assert body["kind"] == "Secret"
    assert body["metadata"]["name"] == "wf-check-secrets"
    assert body["metadata"]["namespace"] == "wf-check"
    assert body["metadata"]["labels"]["app.kubernetes.io/name"] == "wf-check"


def test_argocd_is_told_the_secret_is_not_its_own():
    body = secret_manifest(APP, APP, {})
    assert body["metadata"]["annotations"] == {"argocd.argoproj.io/compare-options": "IgnoreExtraneous"}


def test_an_optional_secret_that_is_absent_leaves_a_secret_without_that_key():
    """The Secret still exists, so every container's envFrom resolves."""
    declared = declared_secrets(manifest({"name": "WF_CHECK_OPTIONAL", "required": False}))
    body = secret_manifest(APP, APP, secret_data(declared, Store({}).lookup))
    assert body["stringData"] == {}
