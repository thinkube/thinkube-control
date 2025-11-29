#!/bin/bash
# Startup script for thinkube-control backend
set -e

echo "Starting thinkube-control backend..."

# Construct DATABASE_URL if not set
if [ -z "$DATABASE_URL" ]; then
    export DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
    echo "Constructed DATABASE_URL from individual variables"
fi

# Configure podman registries with the actual domain (rootless podman path)
if [ -n "$DOMAIN_NAME" ]; then
    echo "Configuring podman registries for domain: $DOMAIN_NAME"
    mkdir -p "$HOME/.config/containers"
    cat > "$HOME/.config/containers/registries.conf" << EOF
unqualified-search-registries = ["docker.io", "registry.${DOMAIN_NAME}"]

[[registry]]
location = "registry.${DOMAIN_NAME}"
insecure = true
EOF
fi

# Start the application
# Tables are created automatically by SQLAlchemy on startup
echo "Starting application..."
exec uvicorn app:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips "*"