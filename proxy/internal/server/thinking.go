/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

func rewriteModelField(body []byte, servingName string) []byte {
	var req map[string]any
	if err := json.Unmarshal(body, &req); err != nil {
		return body
	}
	req["model"] = servingName
	out, err := json.Marshal(req)
	if err != nil {
		return body
	}
	return out
}

// reasoningFields are the names backends give the reasoning text: vLLM and
// SGLang "reasoning_content", Ollama and newer vLLM "reasoning".
var reasoningFields = []string{"reasoning_content", "reasoning"}

// thinkingDisabled reports whether the request turned thinking off, through
// the chat template switch (chat_template_kwargs.enable_thinking: false, as
// vLLM and SGLang take it for Qwen3 models) or reasoning_effort "none".
func thinkingDisabled(body []byte) bool {
	var req struct {
		ChatTemplateKwargs map[string]any `json:"chat_template_kwargs"`
		ReasoningEffort    string         `json:"reasoning_effort"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		return false
	}
	if v, ok := req.ChatTemplateKwargs["enable_thinking"].(bool); ok && !v {
		return true
	}
	return req.ReasoningEffort == "none"
}

// takeReasoning removes every reasoning field from m and returns their text.
func takeReasoning(m map[string]any) string {
	var text string
	for _, f := range reasoningFields {
		if s, ok := m[f].(string); ok {
			text += s
		}
		delete(m, f)
	}
	return text
}

// shapeMessage puts the reasoning text of one message or stream delta where
// the client reads it. With thinking off, a reasoning parser that saw no
// closing think tag files the whole answer as reasoning; that text is the
// answer, so it goes in front of "content". With thinking on, reasoning is
// kept under "reasoning_content", the name every backend is normalised to.
func shapeMessage(m map[string]any, thinkingOff bool) bool {
	hasReasoning := false
	for _, f := range reasoningFields {
		if _, ok := m[f]; ok {
			hasReasoning = true
		}
	}
	if !hasReasoning {
		return false
	}
	if thinkingOff {
		text := takeReasoning(m)
		if text == "" {
			return true
		}
		content, _ := m["content"].(string)
		m["content"] = text + content
		return true
	}
	if _, ok := m["reasoning_content"]; ok {
		return false
	}
	text, ok := m["reasoning"].(string)
	if !ok || text == "" {
		return false
	}
	m["reasoning_content"] = text
	delete(m, "reasoning")
	return true
}

// shapeChoices applies shapeMessage to choices[].<key> of a chat completion
// object; key is "message" for a whole response and "delta" for a stream chunk.
func shapeChoices(body []byte, key string, thinkingOff bool) []byte {
	var resp map[string]any
	if err := json.Unmarshal(body, &resp); err != nil {
		return body
	}
	choices, ok := resp["choices"].([]any)
	if !ok {
		return body
	}
	modified := false
	for _, c := range choices {
		choice, ok := c.(map[string]any)
		if !ok {
			continue
		}
		msg, ok := choice[key].(map[string]any)
		if !ok {
			continue
		}
		if shapeMessage(msg, thinkingOff) {
			modified = true
		}
	}
	if !modified {
		return body
	}
	out, err := json.Marshal(resp)
	if err != nil {
		return body
	}
	return out
}

// normalizeReasoning shapes a non-streamed chat completion response.
func normalizeReasoning(body []byte, thinkingOff bool) []byte {
	return shapeChoices(body, "message", thinkingOff)
}

// shapeStream copies an SSE chat completion stream to w, shaping the delta
// of every "data:" event. Other lines pass through unchanged.
func shapeStream(src io.Reader, w http.ResponseWriter, flusher http.Flusher, thinkingOff bool) error {
	reader := bufio.NewReader(src)
	for {
		line, err := reader.ReadString('\n')
		if len(line) > 0 {
			if data, ok := strings.CutPrefix(line, "data: "); ok {
				payload := strings.TrimRight(data, "\r\n")
				if payload != "[DONE]" {
					line = "data: " + string(shapeChoices([]byte(payload), "delta", thinkingOff)) + line[len("data: ")+len(payload):]
				}
			}
			if _, writeErr := io.WriteString(w, line); writeErr != nil {
				return writeErr
			}
			if line == "\n" || line == "\r\n" {
				flusher.Flush()
			}
		}
		if err == io.EOF {
			flusher.Flush()
			return nil
		}
		if err != nil {
			return fmt.Errorf("read stream: %w", err)
		}
	}
}
