#!/bin/sh

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# build.sh - Build backend container image with Buildah

set -e

echo "Building backend Docker image..."

# Ensure we're in the backend directory
cd /workspace/backend

# The actual build runs Buildah in the Argo build workflow
# This script would contain any pre-build steps if needed

echo "Backend build prepared successfully!"

# 🤖 Generated with Claude