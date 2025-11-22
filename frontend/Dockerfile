# Multi-stage Dockerfile for Vite + React
# Builds static files and serves with nginx

# Build stage
FROM registry.cmxela.com/library/node-base:22-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
# Most dependencies are pre-installed in base image (see thinkube/ansible/.../base-images/node-base-22.Containerfile.j2)
# npm ci will use cached packages for much faster installation
RUN npm ci

# Copy source code
COPY . .

# Build Vite application (outputs to /app/dist)
RUN npm run build

# Production stage - serve static files with nginx
FROM registry.cmxela.com/library/nginx:stable-alpine

# Copy built files to nginx html directory
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

# nginx runs as root by default and handles its own user switching
# Start nginx
CMD ["nginx", "-g", "daemon off;"]
