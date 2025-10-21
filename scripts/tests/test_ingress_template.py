#!/usr/bin/env python3
"""Test the ingress template to ensure it generates valid YAML"""

import yaml
from jinja2 import Template
import sys

# Test data that mimics the todo app
test_data = {
    "project_name": "todo",
    "k8s_namespace": "todo", 
    "domain_name": "thinkube.com",
    "thinkube_spec": {
        "spec": {
            "containers": [
                {
                    "name": "backend",
                    "build": "./backend",
                    "port": 8000,
                    "size": "medium"
                },
                {
                    "name": "frontend", 
                    "build": "./frontend",
                    "port": 80,
                    "size": "small"
                }
            ],
            "routes": [
                {
                    "path": "/api",
                    "to": "backend"
                },
                {
                    "path": "/",
                    "to": "frontend"
                }
            ]
        }
    }
}

# Read the template
with open('templates/k8s/ingress.j2', 'r') as f:
    template_content = f.read()

# Render the template
template = Template(template_content)
rendered = template.render(**test_data)

print("Generated YAML:")
print("=" * 80)
print(rendered)
print("=" * 80)

# Try to parse as YAML
try:
    parsed = yaml.safe_load(rendered)
    print("\n✅ YAML is valid!")
    print("\nParsed structure:")
    print(yaml.dump(parsed, default_flow_style=False))
except yaml.YAMLError as e:
    print(f"\n❌ YAML parsing error: {e}")
    sys.exit(1)

# Check for common issues
lines = rendered.split('\n')
for i, line in enumerate(lines, 1):
    # Check for excessive indentation
    if line.strip() and len(line) - len(line.lstrip()) > 20:
        print(f"\n⚠️  Warning: Line {i} has excessive indentation ({len(line) - len(line.lstrip())} spaces)")
        print(f"   {repr(line)}")