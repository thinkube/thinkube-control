# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
Validates thinkube.yaml against the v1.0 specification constraints.

Knative services must remain portable to cloud serverless platforms
(AWS Lambda, Cloud Run). Component deployments require a fixed name.
"""

import re
from typing import Any, Dict, List

# Services that tie a workload to this cluster: storage to a volume that must
# follow the pod, workflows to an Argo controller and a namespace of its own.
# Neither survives the move to a cloud serverless platform.
KNATIVE_FORBIDDEN_SERVICES = ('storage', 'workflows')


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
        if svc_type in KNATIVE_FORBIDDEN_SERVICES:
            errors.append(
                f"Service '{svc}': {svc_type} is not allowed in Knative services. "
                "database, cache, and queue are allowed."
            )

    return errors


def validate_component_constraints(config: Dict[str, Any]) -> List[str]:
    """Return a list of constraint violations for component deployments.

    Components require spec.deployment.name (the fixed deployment name).
    Returns an empty list for other types or when no violations are found.
    """
    spec = config.get('spec', {})
    deployment = spec.get('deployment', {})
    if deployment.get('type') != 'component':
        return []

    errors = []
    name = deployment.get('name')
    if not name:
        errors.append(
            "Component deployments require spec.deployment.name "
            "(the fixed deployment name)."
        )
    elif not re.match(r'^[a-z][a-z0-9-]*$', name):
        errors.append(
            f"spec.deployment.name '{name}' is invalid. "
            "Must be lowercase alphanumeric with hyphens (^[a-z][a-z0-9-]*$)."
        )

    return errors


def validate_replicas(config: Dict[str, Any]) -> List[str]:
    """Return a list of constraint violations for the replicas field.

    - replicas must be a non-negative integer when present
    - replicas is forbidden when type is knative (use minScale/maxScale instead)
    """
    spec = config.get('spec', {})
    deployment = spec.get('deployment', {})
    errors = []

    if 'replicas' in deployment:
        replicas = deployment['replicas']
        if not isinstance(replicas, int) or replicas < 0:
            errors.append(
                f"spec.deployment.replicas must be a non-negative integer, "
                f"got: {replicas}"
            )
        if deployment.get('type') == 'knative':
            errors.append(
                "spec.deployment.replicas is not allowed for Knative services. "
                "Use minScale/maxScale instead."
            )

    return errors


LLM_BACKEND_DRIVERS = ('per-model-pod', 'ollama')
LLM_BACKEND_TIERS = ('performance', 'flexible')


def validate_llm_backend(config: Dict[str, Any]) -> List[str]:
    """Return a list of constraint violations for spec.llm_backend.

    A backend of the LLM gateway is a gateway-managed component: the gateway
    copies the named container's base Deployment for each model it loads and
    reaches the copy on that container's port.
    """
    spec = config.get('spec', {})
    backend = spec.get('llm_backend')
    if backend is None:
        return []

    errors = []
    deployment = spec.get('deployment', {})
    if deployment.get('type') != 'component' or not deployment.get('gateway_managed'):
        errors.append(
            "spec.llm_backend requires spec.deployment.type: component "
            "and spec.deployment.gateway_managed: true."
        )

    backend_type = backend.get('type')
    if not backend_type or not re.match(r'^[a-z][a-z0-9-]*$', str(backend_type)):
        errors.append(
            f"spec.llm_backend.type '{backend_type}' is invalid. "
            "Must be lowercase alphanumeric with hyphens (^[a-z][a-z0-9-]*$)."
        )
    if not backend.get('display_name'):
        errors.append("spec.llm_backend.display_name is required.")
    if backend.get('driver') not in LLM_BACKEND_DRIVERS:
        errors.append(
            f"spec.llm_backend.driver '{backend.get('driver')}' "
            f"is not one of {', '.join(LLM_BACKEND_DRIVERS)}."
        )
    if backend.get('tier') not in LLM_BACKEND_TIERS:
        errors.append(
            f"spec.llm_backend.tier '{backend.get('tier')}' "
            f"is not one of {', '.join(LLM_BACKEND_TIERS)}."
        )

    for cc in backend.get('gpu_compute_capabilities') or []:
        if not re.match(r'^\d+\.\d+$', str(cc)):
            errors.append(
                f"spec.llm_backend.gpu_compute_capabilities: '{cc}' is not a CUDA compute "
                "capability written as major.minor, such as '12.1'."
            )

    container_name = backend.get('container')
    container = next(
        (c for c in spec.get('containers', []) if c.get('name') == container_name), None
    )
    if container is None:
        errors.append(
            f"spec.llm_backend.container '{container_name}' names no container in spec.containers."
        )
    elif not container.get('port'):
        errors.append(
            f"Container '{container_name}' serves the LLM backend and must declare a port."
        )
    return errors


def validate_startup_seconds(config: Dict[str, Any]) -> List[str]:
    """Return a list of constraint violations for containers' startupSeconds.

    startupSeconds is how long a GPU container may take to answer its health
    path when it starts; it sets the startup probe, so it needs both.
    """
    errors = []
    for c in config.get('spec', {}).get('containers', []):
        if 'startupSeconds' not in c:
            continue
        name = c.get('name', 'unnamed')
        value = c['startupSeconds']
        if not isinstance(value, int) or isinstance(value, bool) or value < 10:
            errors.append(f"Container '{name}': startupSeconds must be an integer of at least 10, got: {value}")
        if not c.get('gpu') or not c.get('health'):
            errors.append(
                f"Container '{name}': startupSeconds sets the startup probe of a GPU container; "
                "it needs gpu and health."
            )
    return errors


BUILD_SIZES = ('small', 'medium', 'large', 'xlarge')


def validate_build_size(config: Dict[str, Any]) -> List[str]:
    """Return a list of constraint violations for containers' buildSize.

    buildSize names the memory a container's build pod gets; the build
    workflow template maps each value to a limit. An unknown value would
    render into nothing, so it is refused here with the allowed values.
    """
    errors = []
    for c in config.get('spec', {}).get('containers', []):
        if 'buildSize' not in c:
            continue
        if c['buildSize'] not in BUILD_SIZES:
            errors.append(
                f"Container '{c.get('name', 'unnamed')}': buildSize '{c['buildSize']}' "
                f"is not one of {', '.join(BUILD_SIZES)}."
            )
    return errors


TEST_SIZES = ('small', 'medium', 'large', 'xlarge')


def validate_test_size(config: Dict[str, Any]) -> List[str]:
    """Return a list of constraint violations for containers' test.size.

    test.size names the memory and CPU a container's test pod gets; the build
    workflow template maps each value to its resources. An unknown value would
    render into nothing, so it is refused here with the allowed values.
    """
    errors = []
    for c in config.get('spec', {}).get('containers', []):
        test = c.get('test') or {}
        if 'size' not in test:
            continue
        if test['size'] not in TEST_SIZES:
            errors.append(
                f"Container '{c.get('name', 'unnamed')}': test.size '{test['size']}' "
                f"is not one of {', '.join(TEST_SIZES)}."
            )
    return errors
