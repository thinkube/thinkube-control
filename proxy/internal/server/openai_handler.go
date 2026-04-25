package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/thinkube/thinkube-control/proxy/internal/auth"
	"github.com/thinkube/thinkube-control/proxy/internal/forwarder"
	"github.com/thinkube/thinkube-control/proxy/internal/metrics"
	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

type OpenAIHandler struct {
	resolver  *resolver.Resolver
	forwarder *forwarder.Forwarder
	maxBody   int64
}

func NewOpenAIHandler(r *resolver.Resolver, f *forwarder.Forwarder, maxBody int64) *OpenAIHandler {
	return &OpenAIHandler{resolver: r, forwarder: f, maxBody: maxBody}
}

type openAIChatRequest struct {
	Model  string `json:"model"`
	Stream bool   `json:"stream"`
}

func (h *OpenAIHandler) ChatCompletions(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	claims := auth.ClaimsFromContext(r.Context())

	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBody+1))
	if err != nil {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Failed to read request body")
		return
	}
	if int64(len(body)) > h.maxBody {
		WriteError(w, "openai", http.StatusRequestEntityTooLarge, "request_too_large", "Request body exceeds maximum size")
		return
	}

	var req openAIChatRequest
	if err := json.Unmarshal(body, &req); err != nil {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Invalid JSON in request body")
		return
	}

	if req.Model == "" {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Missing required field: model")
		return
	}

	tier := r.Header.Get("X-LLM-Tier")
	resolved, err := h.resolver.Resolve(r.Context(), req.Model, tier)
	if err != nil {
		slog.Warn("model resolve failed", "model", req.Model, "error", err)
		WriteError(w, "openai", http.StatusNotFound, "not_found", fmt.Sprintf("Model '%s' not found or not available", req.Model))
		metrics.ErrorsTotal.WithLabelValues("openai", "routing").Inc()
		return
	}

	backendURL := resolved.BackendURL + resolved.APIPath + "/chat/completions"

	userID := ""
	if claims != nil {
		userID = claims.Username
	}

	slog.Debug("forwarding OpenAI request",
		"model", resolved.ModelID,
		"backend", backendURL,
		"stream", req.Stream,
		"user", userID,
	)

	if req.Stream {
		metrics.ActiveStreams.WithLabelValues("openai", resolved.ModelID).Inc()
		defer metrics.ActiveStreams.WithLabelValues("openai", resolved.ModelID).Dec()

		err = h.forwarder.ForwardStream(r.Context(), backendURL, bytes.NewReader(body), w)
		if err != nil {
			slog.Error("stream forward failed", "error", err)
		}
	} else {
		resp, err := h.forwarder.Forward(r.Context(), backendURL, bytes.NewReader(body), r.Header)
		if err != nil {
			WriteError(w, "openai", http.StatusBadGateway, "backend_error", "Backend request failed")
			metrics.ErrorsTotal.WithLabelValues("openai", "backend").Inc()
			return
		}
		defer resp.Body.Close()

		respBody, _ := io.ReadAll(resp.Body)

		for k, vv := range resp.Header {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)
		w.Write(respBody)

		h.recordUsage(respBody, resolved.ModelID, resolved.BackendURL)
	}

	duration := time.Since(start)
	metrics.RequestsTotal.WithLabelValues("openai", resolved.ModelID, resolved.BackendURL, "200").Inc()
	metrics.RequestDuration.WithLabelValues("openai", resolved.ModelID, resolved.BackendURL).Observe(duration.Seconds())

	slog.Info("request completed",
		"protocol", "openai",
		"model", resolved.ModelID,
		"backend", resolved.BackendURL,
		"stream", req.Stream,
		"duration_ms", duration.Milliseconds(),
		"user_id", userID,
	)
}

func (h *OpenAIHandler) ListModels(w http.ResponseWriter, r *http.Request) {
	modelsURL := fmt.Sprintf("%s/api/v1/llm/models", h.resolver.BackendURL())
	resp, err := h.forwarder.ForwardGet(r.Context(), modelsURL)
	if err != nil {
		WriteError(w, "openai", http.StatusBadGateway, "backend_error", "Failed to fetch models")
		return
	}
	defer resp.Body.Close()

	var backendResp struct {
		Models []struct {
			ID         string   `json:"id"`
			Name       string   `json:"name"`
			ServerType []string `json:"server_type"`
			State      string   `json:"state"`
		} `json:"models"`
	}

	body, _ := io.ReadAll(resp.Body)
	json.Unmarshal(body, &backendResp)

	type openAIModel struct {
		ID      string `json:"id"`
		Object  string `json:"object"`
		Created int64  `json:"created"`
		OwnedBy string `json:"owned_by"`
	}

	var models []openAIModel
	for _, m := range backendResp.Models {
		if m.State == "available" || m.State == "deployable" {
			models = append(models, openAIModel{
				ID:      m.ID,
				Object:  "model",
				Created: time.Now().Unix(),
				OwnedBy: "local",
			})
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"object": "list",
		"data":   models,
	})
}

func (h *OpenAIHandler) recordUsage(body []byte, model, backend string) {
	var resp struct {
		Usage struct {
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(body, &resp); err == nil && resp.Usage.PromptTokens > 0 {
		metrics.TokensTotal.WithLabelValues("openai", model, backend, "input").Add(float64(resp.Usage.PromptTokens))
		metrics.TokensTotal.WithLabelValues("openai", model, backend, "output").Add(float64(resp.Usage.CompletionTokens))
	}
}

