# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Secrets an application declares, and what it receives from the store.

An application names the secrets it needs under `secrets:` in manifest.yaml.
It receives exactly those names from the Secrets store and nothing else,
however many the store holds: every function here takes the declared list and
asks about those names only. Delivery is one Kubernetes Secret per
application, <app>-secrets, which every container reads with envFrom, so each
value arrives as a variable under its own name.

Both deploy paths use this module: the first deploy in deploy_application.py
and regeneration in manifest_generator.py.
"""

import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

NAME_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]*$')


class SecretsRefused(ValueError):
    """The manifest's secrets cannot be delivered as declared."""


@dataclass(frozen=True)
class DeclaredSecret:
    name: str
    description: str = ''
    required: bool = True


def secret_resource_name(app_name: str) -> str:
    return f'{app_name}-secrets'


def secrets_page_url(domain: str) -> str:
    return f'https://control.{domain}/secrets'


def declared_secrets(manifest: dict) -> List[DeclaredSecret]:
    """The `secrets:` list of a manifest, validated.

    A manifest without the `secrets` key declares none. Anything else that is
    not a list of valid entries is refused.
    """
    if not isinstance(manifest, dict):
        raise SecretsRefused("manifest.yaml is empty or is not a mapping.")
    if 'secrets' not in manifest:
        return []
    entries = manifest['secrets']
    if not isinstance(entries, list):
        raise SecretsRefused("manifest.yaml: secrets must be a list.")

    declared: List[DeclaredSecret] = []
    errors: List[str] = []
    seen = set()
    for entry in entries:
        name = entry.get('name') if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            errors.append(f"manifest.yaml: a secrets entry has no name: {entry!r}")
            continue
        entry_errors = []
        if not NAME_PATTERN.match(name):
            entry_errors.append(
                f"manifest.yaml: secret name '{name}' must be uppercase letters, "
                "digits and underscores, starting with a letter."
            )
        if name in seen:
            entry_errors.append(f"manifest.yaml: secret '{name}' is declared more than once.")
        seen.add(name)
        required = entry.get('required', True)
        if not isinstance(required, bool):
            entry_errors.append(f"manifest.yaml: secret '{name}': required must be true or false.")
        description = entry.get('description', '')
        if not isinstance(description, str):
            entry_errors.append(f"manifest.yaml: secret '{name}': description must be text.")
        if entry_errors:
            errors.extend(entry_errors)
            continue
        declared.append(DeclaredSecret(name, description, required))

    if errors:
        raise SecretsRefused('\n'.join(errors))
    return declared


def public_env_conflicts(thinkube_config: dict, declared: List[DeclaredSecret]) -> List[str]:
    """A declared secret listed in a container's publicEnv would reach the browser."""
    names = {s.name for s in declared}
    conflicts = []
    for container in (thinkube_config or {}).get('spec', {}).get('containers', []) or []:
        for name in container.get('publicEnv') or []:
            if name in names:
                conflicts.append(
                    f"Container '{container.get('name', 'unnamed')}': publicEnv names "
                    f"'{name}', which is a secret declared in manifest.yaml. "
                    "A secret is never shown to the browser."
                )
    return conflicts


def missing_required(app_name: str, declared: List[DeclaredSecret], present: Iterable[str]) -> List[str]:
    """One message per required secret that the store does not hold."""
    present = set(present)
    return [
        f"Secret '{s.name}' is required by {app_name} and is not in the Secrets store"
        for s in declared
        if s.required and s.name not in present
    ]


def secret_data(declared: List[DeclaredSecret], lookup: Callable[[str], Optional[str]]) -> Dict[str, str]:
    """The Secret's data: each declared name the store holds, and no other.

    `lookup` is asked about declared names only and returns None for a name
    the store does not hold.
    """
    data = {}
    for secret in declared:
        value = lookup(secret.name)
        if value is not None:
            data[secret.name] = value
    return data


def secret_manifest(app_name: str, namespace: str, data: Dict[str, str]) -> dict:
    """The <app>-secrets Secret, applied in the cluster and never written to k8s/.

    k8s/ is committed to git and a repository may be published as a template,
    so the values travel only through the Kubernetes API. ArgoCD does not
    track the Secret; the annotation keeps it from being reported as extra.
    """
    return {
        'apiVersion': 'v1',
        'kind': 'Secret',
        'metadata': {
            'name': secret_resource_name(app_name),
            'namespace': namespace,
            'labels': {
                'app.kubernetes.io/name': app_name,
                'app.kubernetes.io/managed-by': 'thinkube-control',
            },
            'annotations': {'argocd.argoproj.io/compare-options': 'IgnoreExtraneous'},
        },
        'type': 'Opaque',
        'stringData': dict(data),
    }


def env_from(app_name: str, declared: List[DeclaredSecret]) -> list:
    """The envFrom every container carries. Nothing when nothing is declared."""
    if not declared:
        return []
    return [{'secretRef': {'name': secret_resource_name(app_name)}}]


def refusal(app_name: str, domain: str, thinkube_config: dict,
            declared: List[DeclaredSecret], present: Iterable[str]) -> List[str]:
    """Everything that stops this application's secrets being delivered."""
    problems = public_env_conflicts(thinkube_config, declared)
    missing = missing_required(app_name, declared, present)
    if missing:
        problems.extend(missing)
        problems.append(f"Add it on the Secrets page: {secrets_page_url(domain)}")
    return problems
