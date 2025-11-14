# Use optimized base image with common dependencies from library
ARG REGISTRY
FROM ${REGISTRY}/library/python-base:3.12-slim

WORKDIR /app

# All dependencies pre-installed in base image (python-base:3.12-slim)
# Including: Python packages, system tools (git, podman, ssh, debugging tools), podman config
# See: thinkube/ansible/.../base-images/python-base.Containerfile.j2

# Install hera for Argo Workflows integration
RUN pip install --no-cache-dir hera-workflows

# Copy and install fastapi-mcp-extended package
COPY fastapi-mcp-extended /app/fastapi-mcp-extended
RUN pip install --no-cache-dir /app/fastapi-mcp-extended

# Copy application code
COPY app /app/app
COPY start.sh /app/start.sh

# Copy template files and Python manifest generators
COPY ../templates /app/templates

# Make startup script executable
RUN chmod +x /app/start.sh

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose FastAPI port
EXPOSE 8000

# Command to run the server via startup script
CMD ["/app/start.sh"]