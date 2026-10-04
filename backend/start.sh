#!/bin/bash

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

# Startup script for thinkube-control backend
set -e

echo "Starting thinkube-control backend..."

# Construct DATABASE_URL if not set
if [ -z "$DATABASE_URL" ]; then
    export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
    echo "Constructed DATABASE_URL from individual variables"
fi

# Start the application
# Tables are created automatically by SQLAlchemy on startup
echo "Starting application..."
exec uvicorn app:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips "*"