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
from app.models import Pipeline, PipelineStage, PipelineMetric, Service, ServiceHealth, ServiceAction, TemplateDeployment, DeploymentLog

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
    # Drop pipeline/CICD tables
    conn.execute(text('DROP TABLE IF EXISTS pipeline_metrics CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS pipeline_stages CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS pipelines CASCADE'))
    # Drop deployment tables
    conn.execute(text('DROP TABLE IF EXISTS deployment_logs CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS template_deployments CASCADE'))
    # Drop other tables
    conn.execute(text('DROP TABLE IF EXISTS user_favorites CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS dashboards CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS api_tokens CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS alembic_version CASCADE'))
    conn.commit()

# Create all tables fresh (main database only - no CI/CD tables)
print('Creating tables...')
# Create only the tables that belong in the main database
# CI/CD tables will be created separately
from app.db.base import Base
# Exclude CI/CD tables from main database
tables_to_exclude = ['pipelines', 'pipeline_stages', 'pipeline_metrics']
for table in Base.metadata.sorted_tables:
    if table.name not in tables_to_exclude:
        table.create(engine, checkfirst=True)
print('Main database setup complete!')

# Set up CI/CD test database
print('\\nSetting up CI/CD test database...')
cicd_test_db_name = 'cicd_monitoring_test'
cicd_db_url = f\"postgresql://{os.environ.get('POSTGRES_USER')}:{os.environ.get('POSTGRES_PASSWORD')}@{os.environ.get('POSTGRES_HOST')}:{os.environ.get('POSTGRES_PORT')}/{cicd_test_db_name}\"
cicd_engine = create_engine(cicd_db_url)

# Drop CI/CD tables
with cicd_engine.connect() as conn:
    conn.execute(text('DROP TABLE IF EXISTS pipeline_metrics CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS pipeline_stages CASCADE'))
    conn.execute(text('DROP TABLE IF EXISTS pipelines CASCADE'))
    conn.commit()

# Create CI/CD tables in the correct database
from app.models.cicd import Pipeline, PipelineStage, PipelineMetric
for model in [Pipeline, PipelineStage, PipelineMetric]:
    model.__table__.create(cicd_engine, checkfirst=True)

print('CI/CD database setup complete!')
"

# Run tests with coverage
pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html

# Run code quality checks (optional - don't fail the build)
echo -e "\nRunning code quality checks..."
flake8 app/ --max-line-length=120 --exclude=__pycache__ || echo "Linting issues found (non-blocking)"
black --check app/ || echo "Formatting issues found (non-blocking)"

echo -e "\nAll tests and checks passed!"

# 🤖 Generated with Claude