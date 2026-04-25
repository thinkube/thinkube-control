package server

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/thinkube/thinkube-control/proxy/internal/auth"
	"github.com/thinkube/thinkube-control/proxy/internal/forwarder"
	"github.com/thinkube/thinkube-control/proxy/internal/metrics"
	"github.com/thinkube/thinkube-control/proxy/internal/protocol"
	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

type AnthropicHandler struct {
	resolver  *resolver.Resolver
	forwarder *forwarder.Forwarder
	maxBody   int64
}

func NewAnthropicHandler(r *resolver.Resolver, f *forwarder.Forwarder, maxBody int64) *AnthropicHandler {
	return &AnthropicHandler{resolver: r, forwarder: f, maxBody: maxBody}
}

func (h *AnthropicHandler) Messages(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	claims := auth.ClaimsFromContext(r.Context())

	// Validate anthropic-version header
	version := r.Header.Get("anthropic-version")
	if version == "" {
		version = "2023-06-01"
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBody+1))
	if err != nil {
		WriteError(w, "anthropic", http.StatusBadRequest, "invalid_request_error", "Failed to read request body")
		return
	}
	if int64(len(body)) > h.maxBody {
		WriteError(w, "anthropic", http.StatusRequestEntityTooLarge, "request_too_large", "Request body exceeds maximum size")
		return
	}

	var req protocol.AnthropicRequest
	if err := json.Unmarshal(body, &req); err != nil {
		WriteError(w, "anthropic", http.StatusBadRequest, "invalid_request_error", "Invalid JSON in request body")
		return
	}

	if req.Model == "" {
		WriteError(w, "anthropic", http.StatusBadRequest, "invalid_request_error", "Missing required field: model")
		return
	}

	tier := r.Header.Get("X-LLM-Tier")
	resolved, err := h.resolver.Resolve(r.Context(), req.Model, tier)
	if err != nil {
		slog.Warn("model resolve failed", "model", req.Model, "error", err)
		WriteError(w, "anthropic", http.StatusNotFound, "not_found", fmt.Sprintf("Model '%s' not found or not available", req.Model))
		metrics.ErrorsTotal.WithLabelValues("anthropic", "routing").Inc()
		return
	}

	// Translate Anthropic -> OpenAI
	openaiReq, err := protocol.TranslateAnthropicToOpenAI(&req)
	if err != nil {
		slog.Warn("translation failed", "error", err)
		WriteError(w, "anthropic", http.StatusBadRequest, "invalid_request_error", fmt.Sprintf("Translation error: %s", err.Error()))
		metrics.ErrorsTotal.WithLabelValues("anthropic", "translation").Inc()
		return
	}

	openaiReq.Model = resolved.ModelID

	openaiBody, err := json.Marshal(openaiReq)
	if err != nil {
		WriteError(w, "anthropic", http.StatusInternalServerError, "api_error", "Failed to encode translated request")
		return
	}

	backendURL := resolved.BackendURL + resolved.APIPath + "/chat/completions"

	userID := ""
	if claims != nil {
		userID = claims.Username
	}

	slog.Debug("forwarding Anthropic request",
		"model", resolved.ModelID,
		"backend", backendURL,
		"stream", req.Stream,
		"user", userID,
	)

	if req.Stream {
		metrics.ActiveStreams.WithLabelValues("anthropic", resolved.ModelID).Inc()
		defer metrics.ActiveStreams.WithLabelValues("anthropic", resolved.ModelID).Dec()

		h.handleStream(r, w, backendURL, openaiBody, resolved.ModelID, req.Model)
	} else {
		h.handleNonStream(r, w, backendURL, openaiBody, resolved.ModelID, req.Model)
	}

	duration := time.Since(start)
	metrics.RequestsTotal.WithLabelValues("anthropic", resolved.ModelID, resolved.BackendURL, "200").Inc()
	metrics.RequestDuration.WithLabelValues("anthropic", resolved.ModelID, resolved.BackendURL).Observe(duration.Seconds())

	slog.Info("request completed",
		"protocol", "anthropic",
		"model", resolved.ModelID,
		"backend", resolved.BackendURL,
		"stream", req.Stream,
		"duration_ms", duration.Milliseconds(),
		"user_id", userID,
	)
}

func (h *AnthropicHandler) handleNonStream(r *http.Request, w http.ResponseWriter, backendURL string, body []byte, modelID, requestModel string) {
	resp, err := h.forwarder.Forward(r.Context(), backendURL, bytes.NewReader(body), r.Header)
	if err != nil {
		WriteError(w, "anthropic", http.StatusBadGateway, "api_error", "Backend request failed")
		metrics.ErrorsTotal.WithLabelValues("anthropic", "backend").Inc()
		return
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		WriteError(w, "anthropic", http.StatusBadGateway, "api_error", "Failed to read backend response")
		return
	}

	if resp.StatusCode != http.StatusOK {
		// Pass through backend error with Anthropic wrapping
		WriteError(w, "anthropic", resp.StatusCode, "api_error", fmt.Sprintf("Backend returned status %d", resp.StatusCode))
		return
	}

	var openaiResp protocol.OpenAIResponse
	if err := json.Unmarshal(respBody, &openaiResp); err != nil {
		WriteError(w, "anthropic", http.StatusBadGateway, "api_error", "Failed to parse backend response")
		return
	}

	anthropicResp, err := protocol.TranslateOpenAIToAnthropic(&openaiResp, requestModel)
	if err != nil {
		WriteError(w, "anthropic", http.StatusBadGateway, "api_error", "Failed to translate response")
		return
	}

	// Record token usage
	if openaiResp.Usage != nil {
		metrics.TokensTotal.WithLabelValues("anthropic", modelID, backendURL, "input").Add(float64(openaiResp.Usage.PromptTokens))
		metrics.TokensTotal.WithLabelValues("anthropic", modelID, backendURL, "output").Add(float64(openaiResp.Usage.CompletionTokens))
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(anthropicResp)
}

func (h *AnthropicHandler) handleStream(r *http.Request, w http.ResponseWriter, backendURL string, body []byte, modelID, requestModel string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, "anthropic", http.StatusInternalServerError, "api_error", "Streaming not supported")
		return
	}

	resp, err := h.forwarder.ForwardStreamRaw(r.Context(), backendURL, bytes.NewReader(body))
	if err != nil {
		WriteError(w, "anthropic", http.StatusBadGateway, "api_error", "Backend request failed")
		metrics.ErrorsTotal.WithLabelValues("anthropic", "backend").Inc()
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		io.Copy(io.Discard, resp.Body)
		WriteError(w, "anthropic", resp.StatusCode, "api_error", fmt.Sprintf("Backend returned status %d", resp.StatusCode))
		metrics.ErrorsTotal.WithLabelValues("anthropic", "backend").Inc()
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	translator := protocol.NewStreamTranslator(requestModel)
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := scanner.Text()

		if !strings.HasPrefix(line, "data: ") {
			continue
		}

		data := strings.TrimPrefix(line, "data: ")

		if data == "[DONE]" {
			// Emit finish events
			finishEvents := translator.Finish()
			for _, evt := range finishEvents {
				eventType := protocol.ExtractEventType(evt)
				fmt.Fprint(w, protocol.FormatSSE(eventType, evt))
			}
			flusher.Flush()
			break
		}

		var chunk protocol.OpenAIStreamChunk
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			slog.Debug("skipping unparseable stream chunk", "error", err)
			continue
		}

		events, err := translator.TranslateChunk(&chunk)
		if err != nil {
			slog.Error("stream translation error", "error", err)
			continue
		}

		for _, evt := range events {
			eventType := protocol.ExtractEventType(evt)
			fmt.Fprint(w, protocol.FormatSSE(eventType, evt))
		}
		if len(events) > 0 {
			flusher.Flush()
		}
	}
}
