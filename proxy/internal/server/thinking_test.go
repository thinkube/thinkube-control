/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"bufio"
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

// chatBackend is the control plane and the model server in one: it resolves
// every model to itself and answers chat completions with the given body.
// requests receives each chat request body the model server got.
func chatBackend(t *testing.T, answer string, stream bool, requests chan<- map[string]any) *httptest.Server {
	t.Helper()
	var srv *httptest.Server
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/llm/models/resolve", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(resolver.ResolveResult{
			BackendURL: srv.URL, APIPath: "/v1",
			ModelID: "Qwen/Qwen3.6-35B-A3B", ServingName: "Qwen/Qwen3.6-35B-A3B",
			ModelState: "available",
		})
	})
	mux.HandleFunc("/v1/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		var req map[string]any
		json.NewDecoder(r.Body).Decode(&req)
		if requests != nil {
			requests <- req
		}
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
		} else {
			w.Header().Set("Content-Type", "application/json")
		}
		io.WriteString(w, answer)
	})
	srv = httptest.NewServer(mux)
	return srv
}

func chat(t *testing.T, backendURL string, aliases map[string]string, request string) *httptest.ResponseRecorder {
	t.Helper()
	h := NewOpenAIHandler(resolver.New(backendURL, aliases), forwarder.New(5*time.Second), 1<<20)
	rec := httptest.NewRecorder()
	h.ChatCompletions(rec, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(request)))
	return rec
}

func message(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var resp struct {
		Choices []struct {
			Message map[string]any `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil || len(resp.Choices) != 1 {
		t.Fatalf("answer is not one chat completion choice: %s", rec.Body.String())
	}
	return resp.Choices[0].Message
}

// streamText joins the content and the reasoning_content of every delta, and
// fails on a delta that still carries Ollama's "reasoning" name.
func streamText(t *testing.T, rec *httptest.ResponseRecorder) (content, reasoning string, done bool) {
	t.Helper()
	scanner := bufio.NewScanner(strings.NewReader(rec.Body.String()))
	for scanner.Scan() {
		data, ok := strings.CutPrefix(scanner.Text(), "data: ")
		if !ok {
			continue
		}
		if data == "[DONE]" {
			done = true
			continue
		}
		var chunk struct {
			Choices []struct {
				Delta map[string]any `json:"delta"`
			} `json:"choices"`
		}
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			t.Fatalf("chunk is not JSON: %s", data)
		}
		for _, c := range chunk.Choices {
			if _, ok := c.Delta["reasoning"]; ok {
				t.Fatalf("delta still carries reasoning: %s", data)
			}
			s, _ := c.Delta["content"].(string)
			content += s
			s, _ = c.Delta["reasoning_content"].(string)
			reasoning += s
		}
	}
	return content, reasoning, done
}

const thinkingOffRequest = `{"model":"Qwen/Qwen3.6-35B-A3B","messages":[{"role":"user","content":"hi"}],
	"chat_template_kwargs":{"enable_thinking":false}}`
const thinkingOnRequest = `{"model":"Qwen/Qwen3.6-35B-A3B","messages":[{"role":"user","content":"hi"}]}`

// With thinking off, a reasoning parser that sees no closing think tag files
// the whole answer under "reasoning"; the gateway answers it as content.
func TestThinkingOffAnswerInReasoningIsReturnedAsContent(t *testing.T) {
	for _, field := range []string{"reasoning", "reasoning_content"} {
		backend := chatBackend(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":null,"`+field+`":"Hello there."}}]}`, false, nil)
		msg := message(t, chat(t, backend.URL, nil, thinkingOffRequest))
		backend.Close()
		if msg["content"] != "Hello there." {
			t.Errorf("%s: content is %v, want the answer", field, msg["content"])
		}
		if _, ok := msg[field]; ok {
			t.Errorf("%s: the answer is still under %s: %v", field, field, msg)
		}
	}
}

func TestThinkingOffContentIsKept(t *testing.T) {
	backend := chatBackend(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":"Hello there."}}]}`, false, nil)
	defer backend.Close()
	msg := message(t, chat(t, backend.URL, nil, thinkingOffRequest))
	if msg["content"] != "Hello there." {
		t.Errorf("content is %v, want the answer", msg["content"])
	}
}

func TestReasoningEffortNoneIsThinkingOff(t *testing.T) {
	backend := chatBackend(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":"","reasoning":"Hello there."}}]}`, false, nil)
	defer backend.Close()
	msg := message(t, chat(t, backend.URL, nil,
		`{"model":"Qwen/Qwen3.6-35B-A3B","messages":[],"reasoning_effort":"none"}`))
	if msg["content"] != "Hello there." {
		t.Errorf("content is %v, want the answer", msg["content"])
	}
}

func TestThinkingOnKeepsReasoningApartFromContent(t *testing.T) {
	backend := chatBackend(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":"Hello there.","reasoning":"The user greets me."}}]}`, false, nil)
	defer backend.Close()
	msg := message(t, chat(t, backend.URL, nil, thinkingOnRequest))
	if msg["content"] != "Hello there." {
		t.Errorf("content is %v, want the answer only", msg["content"])
	}
	if msg["reasoning_content"] != "The user greets me." {
		t.Errorf("reasoning_content is %v, want the reasoning", msg["reasoning_content"])
	}
	if _, ok := msg["reasoning"]; ok {
		t.Errorf("reasoning is not renamed: %v", msg)
	}
}

const reasoningStream = "data: {\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":null}}]}\n\n" +
	"data: {\"choices\":[{\"index\":0,\"delta\":{\"reasoning\":\"Hello \"}}]}\n\n" +
	"data: {\"choices\":[{\"index\":0,\"delta\":{\"reasoning_content\":\"there.\"}}]}\n\n" +
	"data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n" +
	"data: [DONE]\n\n"

func TestThinkingOffStreamedReasoningIsStreamedAsContent(t *testing.T) {
	backend := chatBackend(t, reasoningStream, true, nil)
	defer backend.Close()
	rec := chat(t, backend.URL, nil, strings.Replace(thinkingOffRequest, `"messages"`, `"stream":true,"messages"`, 1))
	if ct := rec.Header().Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("Content-Type %q, body %s", ct, rec.Body.String())
	}
	content, reasoning, done := streamText(t, rec)
	if content != "Hello there." || reasoning != "" {
		t.Errorf("content %q, reasoning %q; want all of the answer as content", content, reasoning)
	}
	if !done {
		t.Errorf("stream lost its [DONE] event: %s", rec.Body.String())
	}
}

func TestThinkingOnStreamKeepsReasoningApart(t *testing.T) {
	stream := "data: {\"choices\":[{\"index\":0,\"delta\":{\"reasoning\":\"The user greets me.\"}}]}\n\n" +
		"data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hello there.\"}}]}\n\n" +
		"data: [DONE]\n\n"
	backend := chatBackend(t, stream, true, nil)
	defer backend.Close()
	rec := chat(t, backend.URL, nil, strings.Replace(thinkingOnRequest, `"messages"`, `"stream":true,"messages"`, 1))
	content, reasoning, done := streamText(t, rec)
	if content != "Hello there." || reasoning != "The user greets me." {
		t.Errorf("content %q, reasoning %q", content, reasoning)
	}
	if !done {
		t.Errorf("stream lost its [DONE] event: %s", rec.Body.String())
	}
}

func TestAliasReachesTheModelByItsServingName(t *testing.T) {
	requests := make(chan map[string]any, 1)
	backend := chatBackend(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":"ok"}}]}`, false, requests)
	defer backend.Close()
	chat(t, backend.URL, map[string]string{"thinkube-fast": "Qwen/Qwen3.6-35B-A3B"},
		`{"model":"thinkube-fast","messages":[]}`)
	if got := (<-requests)["model"]; got != "Qwen/Qwen3.6-35B-A3B" {
		t.Errorf("model server got model %v, want the serving name", got)
	}
}
