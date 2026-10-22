/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"mime"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"sort"
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
		writeResolveError(w, "openai", req.Model, err)
		return
	}

	backendURL := resolved.BackendURL + resolved.APIPath + "/chat/completions"
	thinkingOff := thinkingDisabled(body)

	if resolved.ServingName != "" && resolved.ServingName != req.Model {
		if body, err = rewriteModelField(body, resolved.ServingName); err != nil {
			WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Request body is not a JSON object: "+err.Error())
			return
		}
	}
	if body, err = typeToolParameters(body); err != nil {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Request body is not a JSON object: "+err.Error())
		return
	}

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

		err = h.forwardStream(r, w, backendURL, body, thinkingOff)
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
		respBody = normalizeReasoning(respBody, thinkingOff)

		for k, vv := range resp.Header {
			if k == "Content-Length" {
				continue
			}
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.Header().Set("Content-Length", fmt.Sprintf("%d", len(respBody)))
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

// forwardStream relays the backend's SSE stream, shaping the reasoning of
// each chunk as normalizeReasoning does for a whole response. A backend
// error status is relayed with its body.
func (h *OpenAIHandler) forwardStream(r *http.Request, w http.ResponseWriter, backendURL string, body []byte, thinkingOff bool) error {
	resp, err := h.forwarder.ForwardStreamRaw(r.Context(), backendURL, bytes.NewReader(body))
	if err != nil {
		WriteError(w, "openai", http.StatusBadGateway, "backend_error", "Backend request failed")
		metrics.ErrorsTotal.WithLabelValues("openai", "backend").Inc()
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		errBody, _ := io.ReadAll(resp.Body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(resp.StatusCode)
		w.Write(errBody)
		return nil
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		return fmt.Errorf("response writer does not support flushing")
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	return shapeStream(resp.Body, w, flusher, thinkingOff)
}

// ListModels answers the OpenAI model list from the backend's registry: the
// models that are served or could be. Each catalog alias of a listed model is
// listed too, after the models and by name, with `root` naming the model it
// points to; a model's own `root` is its id. An app reads `root` to know which
// model an alias names before it calls it. Every entry carries a `thinkube`
// object: the model's state, aliases, capabilities ("agent", "vision"),
// whether it is the agent default, its inputs, its reasoning format, the
// context its backend serves it with (context_window), the length it supports
// (context_length) and the answer budget agents reserve. The backend route carries a
// trailing slash; without it the backend redirects, and the forwarder does not
// follow redirects, so the exact path matters. A backend answer that is not
// 200 or not the expected shape is reported, never shown as an empty list.
func (h *OpenAIHandler) ListModels(w http.ResponseWriter, r *http.Request) {
	modelsURL := fmt.Sprintf("%s/api/v1/llm/models/", h.resolver.BackendURL())
	resp, err := h.forwarder.ForwardGet(r.Context(), modelsURL)
	if err != nil {
		WriteError(w, "openai", http.StatusBadGateway, "backend_error", "Failed to fetch models")
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		WriteError(w, "openai", http.StatusBadGateway, "backend_error",
			fmt.Sprintf("The model registry answered HTTP %d", resp.StatusCode))
		return
	}

	var backendResp struct {
		Models []struct {
			ID                  string   `json:"id"`
			Name                string   `json:"name"`
			ServerType          []string `json:"server_type"`
			State               string   `json:"state"`
			Aliases             []string `json:"aliases"`
			Capabilities        []string `json:"capabilities"`
			DefaultAgent        bool     `json:"default_agent"`
			MaxOutput           *int     `json:"max_output"`
			ServedContextLength *int     `json:"served_context_length"`
			ContextLength       *int     `json:"context_length"`
			ReasoningFormat     *string  `json:"reasoning_format"`
		} `json:"models"`
	}

	body, _ := io.ReadAll(resp.Body)
	if err := json.Unmarshal(body, &backendResp); err != nil {
		WriteError(w, "openai", http.StatusBadGateway, "backend_error", "The model registry answered something that is not a model list")
		return
	}

	type thinkubeModel struct {
		Name            string   `json:"name"`
		State           string   `json:"state"`
		Aliases         []string `json:"aliases"`
		Capabilities    []string `json:"capabilities"`
		DefaultAgent    bool     `json:"default_agent"`
		Input           []string `json:"input"`
		ReasoningFormat *string  `json:"reasoning_format"`
		ContextWindow   *int     `json:"context_window"`
		ContextLength   *int     `json:"context_length"`
		MaxOutput       *int     `json:"max_output"`
	}
	type openAIModel struct {
		ID       string         `json:"id"`
		Object   string         `json:"object"`
		Created  int64          `json:"created"`
		OwnedBy  string         `json:"owned_by"`
		Root     string         `json:"root"`
		Thinkube *thinkubeModel `json:"thinkube"`
	}

	now := time.Now().Unix()
	models := []openAIModel{}
	type aliasOf struct {
		model string
		info  *thinkubeModel
	}
	aliases := map[string]aliasOf{}
	for _, m := range backendResp.Models {
		if m.State != "available" && m.State != "deployable" {
			continue
		}
		input := []string{"text"}
		for _, c := range m.Capabilities {
			if c == "vision" {
				input = append(input, "image")
			}
		}
		info := &thinkubeModel{
			Name: m.Name, State: m.State, Aliases: nonNil(m.Aliases), Capabilities: nonNil(m.Capabilities),
			DefaultAgent: m.DefaultAgent, Input: input, ReasoningFormat: m.ReasoningFormat,
			ContextWindow: m.ServedContextLength, ContextLength: m.ContextLength, MaxOutput: m.MaxOutput,
		}
		models = append(models, openAIModel{ID: m.ID, Object: "model", Created: now, OwnedBy: "local", Root: m.ID, Thinkube: info})
		for _, alias := range m.Aliases {
			aliases[alias] = aliasOf{model: m.ID, info: info}
		}
	}
	names := make([]string, 0, len(aliases))
	for alias := range aliases {
		names = append(names, alias)
	}
	sort.Strings(names)
	for _, alias := range names {
		a := aliases[alias]
		models = append(models, openAIModel{ID: alias, Object: "model", Created: now, OwnedBy: "thinkube", Root: a.model, Thinkube: a.info})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"object": "list",
		"data":   models,
	})
}

// nonNil gives an empty list for a missing one, so the answer has [] not null.
func nonNil(items []string) []string {
	if items == nil {
		return []string{}
	}
	return items
}

type openAIModelRequest struct {
	Model string `json:"model"`
}

func (h *OpenAIHandler) Embeddings(w http.ResponseWriter, r *http.Request) {
	h.forwardJSON(w, r, "/embeddings", "embeddings")
}

// ImageGenerations forwards an image request; the backend answers with the
// images as base64 JSON or URLs.
func (h *OpenAIHandler) ImageGenerations(w http.ResponseWriter, r *http.Request) {
	h.forwardJSON(w, r, "/images/generations", "image generation")
}

// AudioSpeech forwards a text-to-speech request; the backend answers with
// the audio bytes, which are copied back unchanged.
func (h *OpenAIHandler) AudioSpeech(w http.ResponseWriter, r *http.Request) {
	h.forwardJSON(w, r, "/audio/speech", "speech")
}

// forwardJSON sends a JSON request that names its model to the backend that
// serves the model, at path under the backend's API root, and copies the
// backend's answer back unchanged.
func (h *OpenAIHandler) forwardJSON(w http.ResponseWriter, r *http.Request, path, kind string) {
	body, ok := h.readBody(w, r)
	if !ok {
		return
	}

	var req openAIModelRequest
	if err := json.Unmarshal(body, &req); err != nil {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Invalid JSON in request body")
		return
	}
	if req.Model == "" {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Missing required field: model")
		return
	}

	resolved, err := h.resolver.Resolve(r.Context(), req.Model, r.Header.Get("X-LLM-Tier"))
	if err != nil {
		writeResolveError(w, "openai", req.Model, err)
		return
	}

	if resolved.ServingName != "" && resolved.ServingName != req.Model {
		if body, err = rewriteModelField(body, resolved.ServingName); err != nil {
			WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Request body is not a JSON object: "+err.Error())
			return
		}
	}

	h.relay(w, r, resolved, path, kind, func(url string) (*http.Response, error) {
		return h.forwarder.Forward(r.Context(), url, bytes.NewReader(body), r.Header)
	})
}

// AudioTranscriptions forwards a speech-to-text upload. The request is a
// multipart form with the audio file and a model field; the form is written
// again with the model's served name.
func (h *OpenAIHandler) AudioTranscriptions(w http.ResponseWriter, r *http.Request) {
	body, ok := h.readBody(w, r)
	if !ok {
		return
	}

	mediaType, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != "multipart/form-data" || params["boundary"] == "" {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "The request must be multipart/form-data with the audio file and a model field")
		return
	}

	type part struct {
		header textproto.MIMEHeader
		data   []byte
	}
	var parts []part
	model := ""
	reader := multipart.NewReader(bytes.NewReader(body), params["boundary"])
	for {
		p, err := reader.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Invalid multipart form: "+err.Error())
			return
		}
		data, err := io.ReadAll(p)
		if err != nil {
			WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Invalid multipart form: "+err.Error())
			return
		}
		if p.FormName() == "model" {
			model = string(data)
		}
		parts = append(parts, part{header: p.Header, data: data})
	}
	if model == "" {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Missing required field: model")
		return
	}

	resolved, err := h.resolver.Resolve(r.Context(), model, r.Header.Get("X-LLM-Tier"))
	if err != nil {
		writeResolveError(w, "openai", model, err)
		return
	}

	var form bytes.Buffer
	writer := multipart.NewWriter(&form)
	for _, p := range parts {
		data := p.data
		if formName(p.header) == "model" && resolved.ServingName != "" {
			data = []byte(resolved.ServingName)
		}
		pw, err := writer.CreatePart(p.header)
		if err != nil {
			WriteError(w, "openai", http.StatusInternalServerError, "internal_error", "Failed to write the form: "+err.Error())
			return
		}
		pw.Write(data)
	}
	writer.Close()

	h.relay(w, r, resolved, "/audio/transcriptions", "transcription", func(url string) (*http.Response, error) {
		return h.forwarder.ForwardForm(r.Context(), url, &form, writer.FormDataContentType(), r.Header)
	})
}

// formName is the name a multipart part's Content-Disposition gives it.
func formName(header textproto.MIMEHeader) string {
	_, params, err := mime.ParseMediaType(header.Get("Content-Disposition"))
	if err != nil {
		return ""
	}
	return params["name"]
}

// readBody reads the request body up to the configured limit, answering the
// client itself when it cannot.
func (h *OpenAIHandler) readBody(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBody+1))
	if err != nil {
		WriteError(w, "openai", http.StatusBadRequest, "invalid_request_error", "Failed to read request body")
		return nil, false
	}
	if int64(len(body)) > h.maxBody {
		WriteError(w, "openai", http.StatusRequestEntityTooLarge, "request_too_large", "Request body exceeds maximum size")
		return nil, false
	}
	return body, true
}

// relay sends the request with send to the resolved backend's path and
// copies its answer, headers and status back to the client.
func (h *OpenAIHandler) relay(w http.ResponseWriter, r *http.Request, resolved *resolver.ResolveResult, path, kind string, send func(string) (*http.Response, error)) {
	start := time.Now()
	userID := ""
	if claims := auth.ClaimsFromContext(r.Context()); claims != nil {
		userID = claims.Username
	}

	backendURL := resolved.BackendURL + resolved.APIPath + path
	slog.Debug("forwarding "+kind+" request",
		"model", resolved.ModelID,
		"backend", backendURL,
		"user", userID,
	)

	resp, err := send(backendURL)
	if err != nil {
		WriteError(w, "openai", http.StatusBadGateway, "backend_error", "Backend request failed")
		metrics.ErrorsTotal.WithLabelValues("openai", "backend").Inc()
		return
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)

	for k, vv := range resp.Header {
		if k == "Content-Length" {
			continue
		}
		for _, v := range vv {
			w.Header().Add(k, v)
		}
	}
	w.Header().Set("Content-Length", fmt.Sprintf("%d", len(respBody)))
	w.WriteHeader(resp.StatusCode)
	w.Write(respBody)

	duration := time.Since(start)
	metrics.RequestsTotal.WithLabelValues("openai", resolved.ModelID, resolved.BackendURL, fmt.Sprintf("%d", resp.StatusCode)).Inc()
	metrics.RequestDuration.WithLabelValues("openai", resolved.ModelID, resolved.BackendURL).Observe(duration.Seconds())

	slog.Info(kind+" request completed",
		"protocol", "openai",
		"model", resolved.ModelID,
		"backend", resolved.BackendURL,
		"status", resp.StatusCode,
		"duration_ms", duration.Milliseconds(),
		"user_id", userID,
	)
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

