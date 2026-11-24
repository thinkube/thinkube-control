#!/bin/sh

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

# run_tests.sh - Run the backend tests.
#
#   ./run_tests.sh              the whole suite (CI)
#   ./run_tests.sh <file>       one test file, and nothing else
#
# Both run with the same environment, built here and nowhere else: the
# test settings from .env.test, and the database credentials the platform
# hands a test container. Thinkube Tandem runs single files this way.

set -e

# Load test environment variables (quoted values kept whole)
if [ -f .env.test ]; then
    set -a
    . ./.env.test
    set +a
fi

# The store this app's tests own. The platform names it after the app, so
# two apps never write into each other's rows; the file's own value is the
# fallback for a run outside the platform.
export DATABASE_NAME="${TEST_DATABASE_NAME:-${DATABASE_NAME}}"

# Set PostgreSQL credentials from environment
export POSTGRES_PASSWORD="${ADMIN_PASSWORD}"
export POSTGRES_USER="${ADMIN_USERNAME}"
export DATABASE_USER="${ADMIN_USERNAME}"

# Construct DATABASE_URL with the credentials
export DATABASE_URL="postgresql://${DATABASE_USER}:${POSTGRES_PASSWORD}@${DATABASE_HOST}:${DATABASE_PORT}/${DATABASE_NAME}"

# One file: run it and stop. The tree is already provisioned.
if [ -n "$1" ]; then
    exec pytest "$1" -v
fi

echo "Running backend tests..."
echo "Database URL configured for user ${DATABASE_USER} at host: ${DATABASE_HOST}"

# Install dependencies if not already installed
echo "Installing dependencies..."
pip install --break-system-packages -r requirements.txt

# The tests, and nothing else.
#
# A linter and a formatter are things a person runs while writing; on a
# deploy they print pages of "would reformat" that never fail anything
# and never get read. Run them yourself:
#   flake8 app/ --max-line-length=120 --exclude=__pycache__
#   black app/
pytest tests/ -v

echo -e "\nAll tests passed!"

# 🤖 Generated with Claude