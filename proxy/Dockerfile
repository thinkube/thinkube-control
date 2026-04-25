ARG REGISTRY=registry.cmxela.com

# Stage 1: Build
FROM ${REGISTRY}/library/golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /llm-proxy ./cmd/proxy

# Stage 2: Runtime
FROM ${REGISTRY}/library/alpine:3.21
RUN apk --no-cache add ca-certificates
COPY --from=builder /llm-proxy /usr/local/bin/llm-proxy
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/llm-proxy"]
