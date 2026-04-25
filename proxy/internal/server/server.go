package server

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/thinkube/thinkube-control/proxy/internal/auth"
	"github.com/thinkube/thinkube-control/proxy/internal/config"
	"github.com/thinkube/thinkube-control/proxy/internal/forwarder"
	"github.com/thinkube/thinkube-control/proxy/internal/health"
	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

type Server struct {
	cfg     *config.Config
	mux     *http.ServeMux
	logger  *slog.Logger
	checker *health.Checker
}

func New(cfg *config.Config, logger *slog.Logger) *Server {
	s := &Server{
		cfg:     cfg,
		mux:     http.NewServeMux(),
		logger:  logger,
		checker: health.NewChecker(cfg.BackendURL),
	}
	s.registerRoutes()
	return s
}

func (s *Server) Mux() *http.ServeMux {
	return s.mux
}

func (s *Server) registerRoutes() {
	// Unauthenticated endpoints
	s.mux.HandleFunc("GET /health", s.checker.Handler)
	s.mux.HandleFunc("GET /livez", s.checker.LivenessHandler)
	s.mux.Handle("GET /metrics", promhttp.Handler())

	// Auth middleware
	jwtValidator := auth.NewJWTValidator(s.cfg.KeycloakURL, s.cfg.KeycloakRealm, s.cfg.KeycloakClientID)
	s.checker.SetJWKS(jwtValidator.JWKS())
	apiTokenValidator := auth.NewAPITokenValidator(s.cfg.BackendURL)
	authMiddleware := auth.NewMiddleware(jwtValidator, apiTokenValidator)

	// Fetch JWKS keys on startup
	if s.cfg.KeycloakURL != "" {
		go func() {
			if err := jwtValidator.JWKS().FetchKeys(context.Background()); err != nil {
				s.logger.Error("initial JWKS fetch failed", "error", err)
			}
		}()
	}

	// Resolver and forwarder
	res := resolver.New(s.cfg.BackendURL, s.cfg.ModelAliases)
	fwd := forwarder.New(s.cfg.RequestTimeout)

	// OpenAI handler
	openaiHandler := NewOpenAIHandler(res, fwd, s.cfg.MaxRequestBodyBytes)

	// Anthropic handler
	anthropicHandler := NewAnthropicHandler(res, fwd, s.cfg.MaxRequestBodyBytes)

	// Authenticated endpoints
	s.mux.Handle("POST /v1/chat/completions", authMiddleware.Wrap(http.HandlerFunc(openaiHandler.ChatCompletions)))
	s.mux.Handle("GET /v1/models", authMiddleware.Wrap(http.HandlerFunc(openaiHandler.ListModels)))
	s.mux.Handle("POST /v1/messages", authMiddleware.Wrap(http.HandlerFunc(anthropicHandler.Messages)))
}

func (s *Server) Serve() error {
	srv := &http.Server{
		Addr:              s.cfg.ListenAddr,
		Handler:           s.mux,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() {
		s.logger.Info("starting LLM proxy", "addr", s.cfg.ListenAddr)
		errCh <- srv.ListenAndServe()
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		s.logger.Info("shutting down")
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		return srv.Shutdown(shutdownCtx)
	}
}
