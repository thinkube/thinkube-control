package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/thinkube/thinkube-control/proxy/internal/forwarder"
	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

// A backend whose model list lives at the slashed path and redirects the
// slashless one, as FastAPI does.
func modelBackend(t *testing.T, models string) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/llm/models", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/api/v1/llm/models/", http.StatusTemporaryRedirect)
	})
	mux.HandleFunc("/api/v1/llm/models/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(models))
	})
	return httptest.NewServer(mux)
}

func listModels(t *testing.T, backendURL string) (int, map[string]any) {
	t.Helper()
	h := NewOpenAIHandler(resolver.New(backendURL, nil), forwarder.New(5*time.Second), 1<<20)
	rec := httptest.NewRecorder()
	h.ListModels(rec, httptest.NewRequest("GET", "/v1/models", nil))
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("answer is not JSON: %s", rec.Body.String())
	}
	return rec.Code, body
}

func TestListModelsReturnsServedAndDeployableModels(t *testing.T) {
	backend := modelBackend(t, `{"models":[
		{"id":"Qwen/Qwen3-8B","state":"available","server_type":["vllm"]},
		{"id":"Qwen/Qwen3.5-4B","state":"deployable","server_type":["vllm"]},
		{"id":"Qwen/Qwen3-32B","state":"registered","server_type":["vllm"]}]}`)
	defer backend.Close()

	code, body := listModels(t, backend.URL)
	if code != http.StatusOK {
		t.Fatalf("status %d, body %v", code, body)
	}
	data, ok := body["data"].([]any)
	if !ok {
		t.Fatalf("data is %T, want a list: %v", body["data"], body)
	}
	if len(data) != 2 {
		t.Fatalf("want 2 models, got %d: %v", len(data), data)
	}
	if data[0].(map[string]any)["id"] != "Qwen/Qwen3-8B" {
		t.Fatalf("first model: %v", data[0])
	}
}

func TestListModelsWithNothingServedIsAnEmptyListNotNull(t *testing.T) {
	backend := modelBackend(t, `{"models":[]}`)
	defer backend.Close()

	_, body := listModels(t, backend.URL)
	data, ok := body["data"].([]any)
	if !ok || len(data) != 0 {
		t.Fatalf("data should be an empty list, got %v", body["data"])
	}
}

func TestListModelsReportsABackendThatDoesNotAnswer(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer backend.Close()

	code, body := listModels(t, backend.URL)
	if code != http.StatusBadGateway {
		t.Fatalf("status %d, want 502: %v", code, body)
	}
	if _, ok := body["error"]; !ok {
		t.Fatalf("no error in the answer: %v", body)
	}
}
