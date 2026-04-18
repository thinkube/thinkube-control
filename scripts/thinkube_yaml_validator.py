"""
Validates thinkube.yaml against the v1.0 specification constraints.

Knative services must remain portable to cloud serverless platforms
(AWS Lambda, Cloud Run). This module enforces those constraints.
"""

from typing import Any, Dict, List


def validate_knative_constraints(config: Dict[str, Any]) -> List[str]:
    """Return a list of constraint violations for Knative deployments.

    Returns an empty list for type: app or when no violations are found.
    """
    spec = config.get('spec', {})
    deployment = spec.get('deployment', {})
    if deployment.get('type') != 'knative':
        return []

    errors = []
    containers = spec.get('containers', [])

    if len(containers) > 1:
        errors.append(
            "Knative services support a single container only. "
            "Use type: app for multi-container deployments."
        )

    for c in containers:
        name = c.get('name', 'unnamed')
        if c.get('gpu'):
            errors.append(
                f"Container '{name}': gpu is not allowed in Knative services. "
                "Use type: app for GPU workloads."
            )
        if c.get('mounts'):
            errors.append(
                f"Container '{name}': mounts is not allowed in Knative services. "
                "Knative services must be stateless."
            )
        if c.get('schedule'):
            errors.append(
                f"Container '{name}': schedule is not allowed in Knative services."
            )
        if c.get('migrations'):
            errors.append(
                f"Container '{name}': migrations is not allowed in Knative services."
            )
        if c.get('capabilities'):
            errors.append(
                f"Container '{name}': capabilities is not allowed in Knative services."
            )

    timeout = deployment.get('timeoutSeconds', 300)
    if timeout > 900:
        errors.append(
            f"timeoutSeconds ({timeout}) exceeds the maximum of 900 for Knative services."
        )

    for svc in spec.get('services', []):
        svc_type = svc.split(':')[0]
        if svc_type == 'storage':
            errors.append(
                f"Service '{svc}': storage is not allowed in Knative services. "
                "database, cache, and queue are allowed."
            )

    return errors
