# Pass the cluster registry as a build arg so this Dockerfile is portable:
#   kaniko/podman/docker build --build-arg REGISTRY=registry.<your-domain> ...
# The Argo Workflow template (k8s/build-workflow.yaml.jinja) renders
# REGISTRY from the cluster inventory's harbor_registry.
ARG REGISTRY

# Stage 1: Build
FROM ${REGISTRY}/library/golang:1.25-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /llm-proxy ./cmd/proxy

# Stage 2: Runtime
ARG REGISTRY
FROM ${REGISTRY}/library/alpine:latest
RUN apk --no-cache add ca-certificates
COPY --from=builder /llm-proxy /usr/local/bin/llm-proxy
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/llm-proxy"]
