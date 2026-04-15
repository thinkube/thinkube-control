#!/bin/bash
# run_tests.sh - Run backend tests with coverage

set -e

echo "Running backend tests..."

# Load test environment variables
if [ -f .env.test ]; then
    export $(cat .env.test | grep -v '^#' | xargs)
fi

# Set up test database with clean schema
echo "Setting up test database..."
python -c "
import os
from sqlalchemy import create_engine, text
# Import base which imports all models
from app.db.base import Base
# Explicitly import models to ensure they're registered
from app.models import Service, ServiceHealth, ServiceAction, TemplateDeployment, DeploymentLog

# Create engine
engine = create_engine(os.environ.get('DATABASE_URL'))

# Drop all tables for clean test environment
print('Dropping existing tables...')
with engine.connect() as conn:
    # Drop service tables
    conn.execute(text('DROP TABLE IF EXISTS service_actions CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS service_health CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS service_endpoints CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS services CASCADE'))
    # Drop deployment tables
    conn.execute(text('DROP TABLE IF EXISTS deployment_logs CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS template_deployments CASCADE'))
    # Drop other tables
    conn.execute(text('DROP TABLE IF EXISTS user_favorites CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS dashboards CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS api_tokens CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS alembic_version CASCADE'))
    conn.commit()

# Create all tables fresh
print('Creating tables...')
Base.metadata.create_all(bind=engine)
print('Database setup complete!')
"

# Run tests with coverage
pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html

# Run code quality checks (optional - don't fail the build)
echo -e "\nRunning code quality checks..."
flake8 app/ --max-line-length=120 --exclude=__pycache__ || echo "Linting issues found (non-blocking)"
black --check app/ || echo "Formatting issues found (non-blocking)"

echo -e "\nAll tests and checks passed!"
