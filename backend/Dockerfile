# Use optimized base image with common dependencies from library
ARG REGISTRY
FROM ${REGISTRY}/library/python-base:3.12-slim

WORKDIR /app

# Copy requirements and install only additional dependencies not in base
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install ansible and required system packages for template deployment
# Also include networking tools for debugging connectivity issues
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    sshpass \
    dnsutils \
    iputils-ping \
    net-tools \
    curl \
    telnet \
    traceroute \
    nmap \
    netcat-openbsd \
    iproute2 \
    tcpdump \
    vim-tiny \
    podman \
    fuse-overlayfs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install ansible and copier via pip to get latest versions
RUN pip install --no-cache-dir ansible copier

# Configure podman for rootless operation in container
# Using mounted /var/lib/containers from host with fuse-overlayfs
RUN mkdir -p /etc/containers && \
    echo '[engine]' > /etc/containers/containers.conf && \
    echo 'cgroup_manager = "cgroupfs"' >> /etc/containers/containers.conf && \
    echo 'events_logger = "file"' >> /etc/containers/containers.conf && \
    echo '[engine.volume_plugins]' >> /etc/containers/containers.conf && \
    echo '[storage]' > /etc/containers/storage.conf && \
    echo 'driver = "overlay"' >> /etc/containers/storage.conf && \
    echo 'runroot = "/run/containers/storage"' >> /etc/containers/storage.conf && \
    echo 'graphroot = "/var/lib/containers/storage"' >> /etc/containers/storage.conf && \
    echo '[storage.options]' >> /etc/containers/storage.conf && \
    echo '[storage.options.overlay]' >> /etc/containers/storage.conf && \
    echo 'mount_program = "/usr/bin/fuse-overlayfs"' >> /etc/containers/storage.conf

# Create empty registries.conf - will be populated at runtime with actual domain
RUN touch /etc/containers/registries.conf

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