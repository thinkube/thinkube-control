#!/usr/bin/env python3
"""
Generate all Kubernetes manifests from thinkube specification
Replaces Jinja2 templates with reliable Python generation
"""

import yaml
import json
import os
import sys
from pathlib import Path

# Import the ingress generator
from generate_ingress import generate_ingress


def generate_service(container, project_name, k8s_namespace):
    """Generate service for a container"""
    if 'port' not in container:
        return None
        
    service = {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': container['name'],
            'namespace': k8s_namespace,
            'labels': {
                'app.kubernetes.io/name': project_name,
                'app.kubernetes.io/component': container['name']
            }
        },
        'spec': {
            'selector': {
                'app.kubernetes.io/name': project_name,
                'app.kubernetes.io/component': container['name']
            },
            'ports': [{
                'protocol': 'TCP',
                'port': container['port'],
                'targetPort': container['port']
            }]
        }
    }
    
    return service


def generate_deployment(container, project_name, k8s_namespace, container_registry):
    """Generate deployment for a container"""
    deployment = {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {
            'name': container['name'],
            'namespace': k8s_namespace,
            'labels': {
                'app.kubernetes.io/name': project_name,
                'app.kubernetes.io/component': container['name']
            }
        },
        'spec': {
            'replicas': container.get('replicas', 1),
            'selector': {
                'matchLabels': {
                    'app.kubernetes.io/name': project_name,
                    'app.kubernetes.io/component': container['name']
                }
            },
            'template': {
                'metadata': {
                    'labels': {
                        'app.kubernetes.io/name': project_name,
                        'app.kubernetes.io/component': container['name']
                    }
                },
                'spec': {
                    'containers': [{
                        'name': container['name'],
                        'image': f"{container_registry}/thinkube/{project_name}-{container['name']}:latest",
                        'imagePullPolicy': 'Always'
                    }]
                }
            }
        }
    }
    
    # Check if container uses GPU resources
    has_gpu = False
    if 'resources' in container:
        if 'requests' in container['resources'] and 'nvidia.com/gpu' in container['resources']['requests']:
            has_gpu = True
        elif 'limits' in container['resources'] and 'nvidia.com/gpu' in container['resources']['limits']:
            has_gpu = True
    
    # Use Recreate strategy for GPU containers to avoid resource conflicts
    # This ensures the old pod is terminated before the new one is created
    if has_gpu:
        deployment['spec']['strategy'] = {
            'type': 'Recreate'
        }
    # Otherwise use default RollingUpdate for better availability
    else:
        deployment['spec']['strategy'] = {
            'type': 'RollingUpdate',
            'rollingUpdate': {
                'maxSurge': 1,
                'maxUnavailable': 0
            }
        }
    
    # Add port if defined
    if 'port' in container:
        deployment['spec']['template']['spec']['containers'][0]['ports'] = [{
            'containerPort': container['port']
        }]
    
    # Add environment variables
    if 'env' in container:
        env_vars = []
        for key, value in container['env'].items():
            env_vars.append({'name': key, 'value': str(value)})
        deployment['spec']['template']['spec']['containers'][0]['env'] = env_vars
    
    # Add resource limits
    resources = {}
    if 'resources' in container:
        if 'requests' in container['resources']:
            resources['requests'] = container['resources']['requests']
        if 'limits' in container['resources']:
            resources['limits'] = container['resources']['limits']
    if resources:
        deployment['spec']['template']['spec']['containers'][0]['resources'] = resources
    
    # Add volume mounts if needed
    if 'mounts' in container:
        volumes = []
        volume_mounts = []
        
        for mount in container['mounts']:
            volume_name = mount['name'].replace('/', '-').strip('-')
            volume_mounts.append({
                'name': volume_name,
                'mountPath': mount['path']
            })
            
            # Create PVC-backed volume
            volumes.append({
                'name': volume_name,
                'persistentVolumeClaim': {
                    'claimName': f"{project_name}-{container['name']}-{volume_name}"
                }
            })
        
        if volume_mounts:
            deployment['spec']['template']['spec']['containers'][0]['volumeMounts'] = volume_mounts
            deployment['spec']['template']['spec']['volumes'] = volumes
    
    return deployment


def generate_namespace(k8s_namespace):
    """Generate namespace"""
    return {
        'apiVersion': 'v1',
        'kind': 'Namespace',
        'metadata': {
            'name': k8s_namespace
        }
    }


def generate_kustomization(resources):
    """Generate kustomization.yaml"""
    return {
        'apiVersion': 'kustomize.config.k8s.io/v1beta1',
        'kind': 'Kustomization',
        'namespace': resources['namespace'],
        'resources': resources['files'],
        'images': []  # Will be populated by ArgoCD
    }


def main():
    """Generate all manifests"""
    # Get configuration
    project_name = os.environ.get('PROJECT_NAME')
    k8s_namespace = os.environ.get('K8S_NAMESPACE', project_name)
    domain_name = os.environ.get('DOMAIN_NAME')
    container_registry = os.environ.get('CONTAINER_REGISTRY', f"registry.{domain_name}")
    thinkube_spec_str = os.environ.get('THINKUBE_SPEC')
    output_dir = os.environ.get('OUTPUT_DIR', './k8s')
    
    if not all([project_name, domain_name, thinkube_spec_str]):
        print("Error: Missing required environment variables", file=sys.stderr)
        sys.exit(1)
    
    try:
        thinkube_spec = json.loads(thinkube_spec_str)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid THINKUBE_SPEC JSON: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    resources = {
        'namespace': k8s_namespace,
        'files': []
    }
    
    # Generate namespace
    namespace_file = output_path / 'namespace.yaml'
    with open(namespace_file, 'w') as f:
        yaml.dump(generate_namespace(k8s_namespace), f, default_flow_style=False)
    resources['files'].append('namespace.yaml')
    print(f"Generated: {namespace_file}")
    
    # Generate deployments and services for each container
    containers = thinkube_spec.get('spec', {}).get('containers', [])
    for container in containers:
        # Deployment
        deployment = generate_deployment(container, project_name, k8s_namespace, container_registry)
        deployment_file = output_path / f"{container['name']}-deployment.yaml"
        with open(deployment_file, 'w') as f:
            yaml.dump(deployment, f, default_flow_style=False, sort_keys=False)
        resources['files'].append(f"{container['name']}-deployment.yaml")
        print(f"Generated: {deployment_file}")
        
        # Service (if port is defined)
        service = generate_service(container, project_name, k8s_namespace)
        if service:
            service_file = output_path / f"{container['name']}-service.yaml"
            with open(service_file, 'w') as f:
                yaml.dump(service, f, default_flow_style=False, sort_keys=False)
            resources['files'].append(f"{container['name']}-service.yaml")
            print(f"Generated: {service_file}")
    
    # Generate ingress
    ingress = generate_ingress(project_name, k8s_namespace, domain_name, thinkube_spec)
    if ingress:
        ingress_file = output_path / 'ingress.yaml'
        with open(ingress_file, 'w') as f:
            f.write("# Generated ingress configuration\n")
            f.write("---\n")
            yaml.dump(ingress, f, default_flow_style=False, sort_keys=False)
        resources['files'].append('ingress.yaml')
        print(f"Generated: {ingress_file}")
    
    # Generate kustomization.yaml
    kustomization_file = output_path / 'kustomization.yaml'
    with open(kustomization_file, 'w') as f:
        yaml.dump(generate_kustomization(resources), f, default_flow_style=False, sort_keys=False)
    print(f"Generated: {kustomization_file}")
    
    print(f"\nSuccessfully generated {len(resources['files'])} manifest files")


if __name__ == '__main__':
    main()