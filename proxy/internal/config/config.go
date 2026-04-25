package config

import (
	"encoding/json"
	"os"
	"strconv"
	"time"
)

type Config struct {
	BackendURL            string
	LogLevel              string
	RequestTimeout        time.Duration
	ModelAliases          map[string]string
	KeycloakURL           string
	KeycloakRealm         string
	KeycloakClientID      string
	ListenAddr            string
	MaxRequestBodyBytes   int64
}

func Load() *Config {
	cfg := &Config{
		BackendURL:          envOrDefault("BACKEND_URL", "http://backend.thinkube-control.svc.cluster.local:8000"),
		LogLevel:            envOrDefault("LOG_LEVEL", "info"),
		RequestTimeout:      envDurationSeconds("REQUEST_TIMEOUT_SECONDS", 300),
		KeycloakURL:         envOrDefault("KEYCLOAK_URL", ""),
		KeycloakRealm:       envOrDefault("KEYCLOAK_REALM", "thinkube"),
		KeycloakClientID:    envOrDefault("KEYCLOAK_CLIENT_ID", "thinkube-control"),
		ListenAddr:          envOrDefault("LISTEN_ADDR", ":8080"),
		MaxRequestBodyBytes: envInt64("MAX_REQUEST_BODY_BYTES", 10*1024*1024),
	}
	cfg.ModelAliases = parseModelAliases(envOrDefault("MODEL_ALIASES", "{}"))
	return cfg
}

func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envDurationSeconds(key string, fallback int) time.Duration {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return time.Duration(n) * time.Second
		}
	}
	return time.Duration(fallback) * time.Second
}

func envInt64(key string, fallback int64) int64 {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			return n
		}
	}
	return fallback
}

func parseModelAliases(raw string) map[string]string {
	aliases := make(map[string]string)
	_ = json.Unmarshal([]byte(raw), &aliases)
	return aliases
}
