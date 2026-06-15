package server

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"

	"github.com/thinkube/thinkube-control/proxy/internal/metrics"
	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

type anthropicError struct {
	Type  string `json:"type"`
	Error struct {
		Type    string `json:"type"`
		Message string `json:"message"`
	} `json:"error"`
}

type openaiError struct {
	Error struct {
		Message string `json:"message"`
		Type    string `json:"type"`
		Code    string `json:"code"`
	} `json:"error"`
}

// writeResolveError maps a resolver failure to the right client response.
// Only a definitive ErrModelNotFound is a 404; every other (transient) failure
// is a retryable 503 — so a momentary backend stall/flap is never surfaced as
// "model not found".
func writeResolveError(w http.ResponseWriter, protocol, model string, err error) {
	if errors.Is(err, resolver.ErrModelNotFound) {
		slog.Warn("model not found", "model", model, "error", err)
		WriteError(w, protocol, http.StatusNotFound, "not_found",
			fmt.Sprintf("Model '%s' not found", model))
		metrics.ErrorsTotal.WithLabelValues(protocol, "not_found").Inc()
		return
	}
	slog.Warn("model resolve unavailable", "model", model, "error", err)
	WriteError(w, protocol, http.StatusServiceUnavailable, "service_unavailable",
		fmt.Sprintf("Model '%s' is temporarily unavailable, please retry", model))
	metrics.ErrorsTotal.WithLabelValues(protocol, "resolve_unavailable").Inc()
}

func WriteError(w http.ResponseWriter, protocol string, statusCode int, errorType string, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)

	if protocol == "anthropic" {
		resp := anthropicError{Type: "error"}
		resp.Error.Type = errorType
		resp.Error.Message = message
		json.NewEncoder(w).Encode(resp)
	} else {
		resp := openaiError{}
		resp.Error.Message = message
		resp.Error.Type = errorType
		resp.Error.Code = errorType
		json.NewEncoder(w).Encode(resp)
	}
}
