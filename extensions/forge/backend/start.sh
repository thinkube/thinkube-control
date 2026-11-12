#!/bin/bash

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

set -e

echo "🔄 Applying database migrations..."
alembic upgrade head

echo "🚀 Starting application..."
exec uvicorn app:app --host 0.0.0.0 --port 8000