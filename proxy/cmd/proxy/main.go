package main

import (
	"log/slog"
	"os"
	"strings"

	"github.com/thinkube/thinkube-control/proxy/internal/config"
	"github.com/thinkube/thinkube-control/proxy/internal/server"
)

func main() {
	cfg := config.Load()

	var level slog.Level
	switch strings.ToLower(cfg.LogLevel) {
	case "debug":
		level = slog.LevelDebug
	case "warn":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	default:
		level = slog.LevelInfo
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
	slog.SetDefault(logger)

	logger.Info("LLM proxy starting",
		"backend_url", cfg.BackendURL,
		"keycloak_url", cfg.KeycloakURL,
		"keycloak_realm", cfg.KeycloakRealm,
		"log_level", cfg.LogLevel,
		"listen_addr", cfg.ListenAddr,
	)

	srv := server.New(cfg, logger)

	if err := srv.Serve(); err != nil {
		logger.Error("server error", "error", err)
		os.Exit(1)
	}
}
