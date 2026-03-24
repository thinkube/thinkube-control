#!/usr/bin/env python3
"""
Generate Kubernetes HTTPRoute YAML from thinkube specification
Uses Gateway API HTTPRoute instead of Ingress (Cilium Gateway API migration)
"""

import yaml
import sys
import json
import os


def generate_ingress(project_name, k8s_namespace, domain_name, thinkube_spec):
    """Generate HTTPRoute configuration from thinkube spec.

    Note: Function retains 'generate_ingress' name for backward compatibility
    with existing imports, but now generates Gateway API HTTPRoute resources.
    """

    # Check if we need routing (routes defined or containers with ports)
    routes = thinkube_spec.get('spec', {}).get('routes', [])
    containers = thinkube_spec.get('spec', {}).get('containers', [])
    containers_with_ports = [c for c in containers if 'port' in c]

    if not routes and not containers_with_ports:
        return None  # No HTTPRoute needed

    # Note: large-uploads capability is checked but Gateway API does not support
    # per-route body-size limits. This must be handled at the backend/gateway level.
    # Keeping the check here as a comment for future reference.
    # has_large_uploads = any(
    #     'large-uploads' in c.get('capabilities', [])
    #     for c in containers
    # )

    # Build HTTPRoute object
    httproute = {
        'apiVersion': 'gateway.networking.k8s.io/v1',
        'kind': 'HTTPRoute',
        'metadata': {
            'name': project_name,
            'namespace': k8s_namespace,
            'labels': {
                'app.kubernetes.io/name': project_name,
                'app.kubernetes.io/managed-by': 'argocd'
            }
        },
        'spec': {
            'parentRefs': [{
                'name': 'thinkube-gateway',
                'namespace': 'gateway-system',
                'sectionName': 'https'
            }],
            'hostnames': [f"{project_name}.{domain_name}"],
            'rules': []
        }
    }

    # Build rules
    rules = []

    if routes:
        # Use defined routes
        for route in routes:
            container_name = route['to']
            # Find the container to get its port
            container = next((c for c in containers if c['name'] == container_name), None)
            if container and 'port' in container:
                rules.append({
                    'matches': [{
                        'path': {
                            'type': 'PathPrefix',
                            'value': route['path']
                        }
                    }],
                    'backendRefs': [{
                        'name': container_name,
                        'port': container['port']
                    }]
                })
    else:
        # Default route to first container with a port
        if containers_with_ports:
            default_container = containers_with_ports[0]
            rules.append({
                'matches': [{
                    'path': {
                        'type': 'PathPrefix',
                        'value': '/'
                    }
                }],
                'backendRefs': [{
                    'name': default_container['name'],
                    'port': default_container['port']
                }]
            })

    httproute['spec']['rules'] = rules

    return httproute


def main():
    """Main function to generate HTTPRoute YAML"""

    # Get environment variables
    project_name = os.environ.get('PROJECT_NAME')
    k8s_namespace = os.environ.get('K8S_NAMESPACE', project_name)
    domain_name = os.environ.get('DOMAIN_NAME')
    thinkube_spec_str = os.environ.get('THINKUBE_SPEC')

    if not all([project_name, domain_name, thinkube_spec_str]):
        print("Error: Missing required environment variables", file=sys.stderr)
        print("Required: PROJECT_NAME, DOMAIN_NAME, THINKUBE_SPEC", file=sys.stderr)
        sys.exit(1)

    try:
        thinkube_spec = json.loads(thinkube_spec_str)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid THINKUBE_SPEC JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Generate HTTPRoute
    httproute = generate_ingress(project_name, k8s_namespace, domain_name, thinkube_spec)

    if httproute:
        # Output YAML with header
        print("# Generated HTTPRoute configuration (Gateway API)")
        print("# Routes traffic through thinkube-gateway in gateway-system namespace")
        print("---")
        print(yaml.dump(httproute, default_flow_style=False, sort_keys=False))
    else:
        print("# No HTTPRoute needed - no routes or containers with ports defined")


if __name__ == '__main__':
    main()
