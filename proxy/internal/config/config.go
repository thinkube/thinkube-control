/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package config

import (
	"encoding/json"
	"fmt"
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
	aliases, err := parseModelAliases(os.Getenv("MODEL_ALIASES"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "MODEL_ALIASES: %v\n", err)
		os.Exit(1)
	}
	cfg.ModelAliases = aliases
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

// parseModelAliases reads the alias table, a JSON object of alias -> model
// id. The deployment always sets it; an empty object means no aliases.
func parseModelAliases(raw string) (map[string]string, error) {
	if raw == "" {
		return nil, fmt.Errorf("not set; the deployment passes the alias table as a JSON object")
	}
	aliases := make(map[string]string)
	if err := json.Unmarshal([]byte(raw), &aliases); err != nil {
		return nil, fmt.Errorf("not a JSON object of alias -> model id: %w", err)
	}
	return aliases, nil
}
