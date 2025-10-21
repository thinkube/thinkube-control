#!/usr/bin/env python3
"""
Generate Kubernetes Ingress YAML from thinkube specification
This replaces the error-prone Jinja2 template with reliable Python generation
"""

import yaml
import sys
import json
import os


def generate_ingress(project_name, k8s_namespace, domain_name, thinkube_spec):
    """Generate Ingress configuration from thinkube spec"""
    
    # Check if we need ingress (routes defined or containers with ports)
    routes = thinkube_spec.get('spec', {}).get('routes', [])
    containers = thinkube_spec.get('spec', {}).get('containers', [])
    containers_with_ports = [c for c in containers if 'port' in c]
    
    if not routes and not containers_with_ports:
        return None  # No ingress needed
    
    # Check for large upload capability
    has_large_uploads = any(
        'large-uploads' in c.get('capabilities', []) 
        for c in containers
    )
    
    # Check if this is a service that can be disabled (not thinkube-control itself)
    # Services deployed through templates are typically user apps that can be disabled
    is_disableable = project_name != 'thinkube-control' and k8s_namespace != 'thinkube-control'
    
    # Build ingress object
    ingress = {
        'apiVersion': 'networking.k8s.io/v1',
        'kind': 'Ingress',
        'metadata': {
            'name': project_name,
            'namespace': k8s_namespace,
            'labels': {
                'app.kubernetes.io/name': project_name,
                'app.kubernetes.io/managed-by': 'argocd'
            },
            'annotations': {
                'cert-manager.io/cluster-issuer': 'letsencrypt-prod',
                'nginx.ingress.kubernetes.io/ssl-redirect': 'true',
                'nginx.ingress.kubernetes.io/default-backend': 'paused-backend',
                'nginx.ingress.kubernetes.io/custom-http-errors': '404,503'
            }
        },
        'spec': {
            'ingressClassName': 'nginx',
            'tls': [{
                'hosts': [f"{project_name}.{domain_name}"],
                'secretName': f"{k8s_namespace}-tls-secret"
            }],
            'rules': [{
                'host': f"{project_name}.{domain_name}",
                'http': {
                    'paths': []
                }
            }]
        }
    }
    
    # Add large upload annotations if needed
    if has_large_uploads:
        ingress['metadata']['annotations'].update({
            'nginx.ingress.kubernetes.io/proxy-body-size': '1024m',
            'nginx.ingress.kubernetes.io/proxy-request-buffering': 'off',
            'nginx.ingress.kubernetes.io/client-body-buffer-size': '50m'
        })
    
    # Add custom error handling for disabled services
    # This works with the error-backend service deployed in the ingress namespace
    if is_disableable:
        ingress['metadata']['annotations']['nginx.ingress.kubernetes.io/custom-http-errors'] = '503'
    
    # Build paths
    paths = []
    
    if routes:
        # Use defined routes
        for route in routes:
            container_name = route['to']
            # Find the container to get its port
            container = next((c for c in containers if c['name'] == container_name), None)
            if container and 'port' in container:
                paths.append({
                    'path': route['path'],
                    'pathType': 'Prefix',
                    'backend': {
                        'service': {
                            'name': container_name,
                            'port': {
                                'number': container['port']
                            }
                        }
                    }
                })
    else:
        # Default route to first container with a port
        if containers_with_ports:
            default_container = containers_with_ports[0]
            paths.append({
                'path': '/',
                'pathType': 'Prefix',
                'backend': {
                    'service': {
                        'name': default_container['name'],
                        'port': {
                            'number': default_container['port']
                        }
                    }
                }
            })
    
    ingress['spec']['rules'][0]['http']['paths'] = paths
    
    return ingress


def main():
    """Main function to generate ingress.yaml"""
    
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
    
    # Generate ingress
    ingress = generate_ingress(project_name, k8s_namespace, domain_name, thinkube_spec)
    
    if ingress:
        # Output YAML with header
        print("# Generated ingress configuration")
        print("# Always generate ingress for applications with routes or containers with ports")
        print("---")
        print(yaml.dump(ingress, default_flow_style=False, sort_keys=False))
    else:
        print("# No ingress needed - no routes or containers with ports defined")


if __name__ == '__main__':
    main()