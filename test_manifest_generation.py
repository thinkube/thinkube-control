#!/usr/bin/env python3
"""Test manifest generation with mistral configuration"""

import os
import json
import subprocess
import sys

# Mistral thinkube.yaml equivalent
mistral_spec = {
    "apiVersion": "thinkube.io/v1",
    "kind": "Application", 
    "metadata": {
        "name": "mistral"
    },
    "spec": {
        "description": "Mistral 7B Instruct model with Gradio UI",
        "version": "1.0.0",
        "containers": [
            {
                "name": "inference",
                "port": 7860,
                "env": {
                    "MODEL_NAME": "mistralai/Mistral-7B-Instruct-v0.3",
                    "HF_TOKEN": "${HF_TOKEN}"
                },
                "resources": {
                    "requests": {
                        "memory": "16Gi",
                        "cpu": "4",
                        "nvidia.com/gpu": "1"
                    },
                    "limits": {
                        "memory": "24Gi", 
                        "cpu": "8",
                        "nvidia.com/gpu": "1"
                    }
                },
                "capabilities": ["gpu", "large-uploads"]
            }
        ]
    }
}

# Set environment variables
os.environ['PROJECT_NAME'] = 'mistral'
os.environ['K8S_NAMESPACE'] = 'mistral'
os.environ['DOMAIN_NAME'] = 'cmxela.com'
os.environ['CONTAINER_REGISTRY'] = 'registry.cmxela.com'
os.environ['THINKUBE_SPEC'] = json.dumps(mistral_spec)
os.environ['OUTPUT_DIR'] = './test_output'

# Change to templates directory
os.chdir('/home/alexmc/thinkube/thinkube-control-temp/templates/k8s')

# Run the generator
print("Testing manifest generation for mistral...")
result = subprocess.run([sys.executable, 'generate_manifests.py'], capture_output=True, text=True)

print("STDOUT:")
print(result.stdout)

if result.stderr:
    print("\nSTDERR:")
    print(result.stderr)

print(f"\nReturn code: {result.returncode}")

# Show generated ingress
if result.returncode == 0:
    print("\n--- Generated ingress.yaml ---")
    try:
        with open('./test_output/ingress.yaml', 'r') as f:
            print(f.read())
    except FileNotFoundError:
        print("No ingress.yaml generated")