/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"encoding/json"
	"fmt"
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
	h := NewOpenAIHandler(resolver.New(backendURL), forwarder.New(5*time.Second), 1<<20)
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

// A catalog alias is listed after the models, with root naming the model it
// points to, so an app knows which model it names before calling it. A
// model's root is its own id. A model not served or deployable lists no alias.
func TestListModelsListsAliasesWithTheirRoot(t *testing.T) {
	backend := modelBackend(t, `{"models":[
		{"id":"nvidia/Qwen3.6-35B-A3B-NVFP4","state":"available","server_type":["vllm"],"aliases":["thinkube-fast","thinkube-agent"]},
		{"id":"Qwen/Qwen3-32B","state":"registered","server_type":["vllm"],"aliases":["thinkube-big"]}]}`)
	defer backend.Close()

	_, body := listModels(t, backend.URL)
	data := body["data"].([]any)
	got := []string{}
	for _, d := range data {
		m := d.(map[string]any)
		got = append(got, m["id"].(string)+" -> "+m["root"].(string))
	}
	want := []string{
		"nvidia/Qwen3.6-35B-A3B-NVFP4 -> nvidia/Qwen3.6-35B-A3B-NVFP4",
		"thinkube-agent -> nvidia/Qwen3.6-35B-A3B-NVFP4",
		"thinkube-fast -> nvidia/Qwen3.6-35B-A3B-NVFP4",
	}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

// Each entry says what an agent client needs: the model's state, aliases,
// capabilities, whether it is the agent default, its inputs, the context its
// backend serves and the answer budget.
func TestListModelsDescribesEachModelForAgents(t *testing.T) {
	backend := modelBackend(t, `{"models":[
		{"id":"org/flash","name":"Flash","state":"available","aliases":["thinkube-flash"],
		 "capabilities":["agent","vision"],"default_agent":true,"max_output":32768,"served_context_length":262144,
		 "context_length":262144,"reasoning_format":"qwen3"},
		{"id":"org/embed","name":"Embed","state":"deployable"}]}`)
	defer backend.Close()

	_, body := listModels(t, backend.URL)
	byID := map[string]map[string]any{}
	for _, d := range body["data"].([]any) {
		m := d.(map[string]any)
		byID[m["id"].(string)] = m["thinkube"].(map[string]any)
	}
	flash := byID["org/flash"]
	if flash["state"] != "available" || flash["default_agent"] != true || flash["context_window"] != 262144.0 || flash["max_output"] != 32768.0 {
		t.Fatalf("flash: %v", flash)
	}
	if flash["reasoning_format"] != "qwen3" || flash["context_length"] != 262144.0 {
		t.Fatalf("flash reasoning or length: %v", flash)
	}
	if fmt.Sprint(flash["input"]) != "[text image]" || fmt.Sprint(flash["capabilities"]) != "[agent vision]" {
		t.Fatalf("flash inputs or capabilities: %v", flash)
	}
	if fmt.Sprint(byID["thinkube-flash"]) != fmt.Sprint(flash) {
		t.Fatalf("the alias does not describe its model: %v", byID["thinkube-flash"])
	}
	embed := byID["org/embed"]
	if fmt.Sprint(embed["capabilities"]) != "[]" || embed["context_window"] != nil || embed["reasoning_format"] != nil || fmt.Sprint(embed["input"]) != "[text]" {
		t.Fatalf("embed: %v", embed)
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
