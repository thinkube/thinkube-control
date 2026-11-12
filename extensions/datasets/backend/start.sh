#!/bin/bash

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

set -e

echo "🔄 Applying database migrations..."
alembic upgrade head

# Versions whose files lack `_row` and `_origin` get them, before any
# route can read those versions (app/identify.py)
echo "🔄 Giving rows their identity..."
python -m app.identify

echo "🚀 Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000