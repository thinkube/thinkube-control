/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/thinkube/thinkube-control/proxy/internal/forwarder"
	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

// A control plane that resolves every model to backendURL, on a backend that
// serves the Messages API itself when native is true.
func controlPlane(t *testing.T, backendURL string, native bool) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resolver.ResolveResult{
			BackendURL: backendURL, APIPath: "/v1", ModelID: "org/model",
			ServingName: "org/model", ModelState: "available", Tier: "performance",
			AnthropicMessages: native,
		})
	}))
}

func postMessages(t *testing.T, controlURL, body string) *httptest.ResponseRecorder {
	t.Helper()
	h := NewAnthropicHandler(resolver.New(controlURL, nil), forwarder.New(5*time.Second), 1<<20)
	req := httptest.NewRequest("POST", "/v1/messages", strings.NewReader(body))
	req.Header.Set("anthropic-version", "2023-06-01")
	rec := httptest.NewRecorder()
	h.Messages(rec, req)
	return rec
}

func TestANativeBackendGetsTheRequestAsItCameWithItsServingName(t *testing.T) {
	var path, version string
	var got map[string]any
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path, version = r.URL.Path, r.Header.Get("anthropic-version")
		raw, _ := io.ReadAll(r.Body)
		json.Unmarshal(raw, &got)
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"type":"message","content":[{"type":"text","text":"OK"}],"usage":{"input_tokens":3,"output_tokens":1}}`))
	}))
	defer backend.Close()
	control := controlPlane(t, backend.URL, true)
	defer control.Close()

	rec := postMessages(t, control.URL, `{"model":"alias","max_tokens":8,"thinking":{"type":"enabled","budget_tokens":64},"messages":[{"role":"user","content":"Say OK"}]}`)

	if rec.Code != http.StatusOK || path != "/v1/messages" || version != "2023-06-01" {
		t.Fatalf("status %d, path %q, version %q: %s", rec.Code, path, version, rec.Body.String())
	}
	if got["model"] != "org/model" {
		t.Fatalf("model sent %v, want the serving name", got["model"])
	}
	if _, ok := got["thinking"]; !ok {
		t.Fatalf("a field the proxy does not translate was dropped: %v", got)
	}
	if !strings.Contains(rec.Body.String(), `"text":"OK"`) {
		t.Fatalf("answer not passed through: %s", rec.Body.String())
	}
}

func TestANativeBackendsErrorReachesTheClientAsItIs(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens is required"}}`))
	}))
	defer backend.Close()
	control := controlPlane(t, backend.URL, true)
	defer control.Close()

	rec := postMessages(t, control.URL, `{"model":"alias","messages":[{"role":"user","content":"hi"}]}`)

	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "max_tokens is required") {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
}

func TestANativeBackendsStreamIsCopiedEventByEvent(t *testing.T) {
	events := "event: message_start\ndata: {\"type\":\"message_start\"}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.Write([]byte(events))
	}))
	defer backend.Close()
	control := controlPlane(t, backend.URL, true)
	defer control.Close()

	rec := postMessages(t, control.URL, `{"model":"alias","max_tokens":8,"stream":true,"messages":[{"role":"user","content":"hi"}]}`)

	if rec.Code != http.StatusOK || rec.Body.String() != events {
		t.Fatalf("status %d, body %q", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("Content-Type") != "text/event-stream" {
		t.Fatalf("content type %q", rec.Header().Get("Content-Type"))
	}
}

func TestABackendWithoutTheMessagesAPIIsStillTranslated(t *testing.T) {
	var path string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"id":"x","object":"chat.completion","model":"org/model","choices":[{"index":0,"message":{"role":"assistant","content":"OK"},"finish_reason":"stop"}]}`))
	}))
	defer backend.Close()
	control := controlPlane(t, backend.URL, false)
	defer control.Close()

	rec := postMessages(t, control.URL, `{"model":"alias","max_tokens":8,"messages":[{"role":"user","content":"Say OK"}]}`)

	if rec.Code != http.StatusOK || path != "/v1/chat/completions" {
		t.Fatalf("status %d, path %q: %s", rec.Code, path, rec.Body.String())
	}
}
