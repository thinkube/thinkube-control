package protocol

import (
	"encoding/json"
	"testing"
)

func TestTranslateSimpleTextMessage(t *testing.T) {
	req := &AnthropicRequest{
		Model:     "test-model",
		MaxTokens: 100,
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`"Hello world"`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if out.Model != "test-model" {
		t.Errorf("model = %q, want %q", out.Model, "test-model")
	}
	if len(out.Messages) != 1 {
		t.Fatalf("messages count = %d, want 1", len(out.Messages))
	}
	if out.Messages[0].Role != "user" {
		t.Errorf("role = %q, want %q", out.Messages[0].Role, "user")
	}
	if out.Messages[0].Content != "Hello world" {
		t.Errorf("content = %v, want %q", out.Messages[0].Content, "Hello world")
	}
}

func TestTranslateSystemPromptString(t *testing.T) {
	req := &AnthropicRequest{
		Model:     "test-model",
		MaxTokens: 100,
		System:    json.RawMessage(`"You are a pirate."`),
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`"Ahoy"`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(out.Messages) != 2 {
		t.Fatalf("messages count = %d, want 2", len(out.Messages))
	}
	if out.Messages[0].Role != "system" {
		t.Errorf("first message role = %q, want %q", out.Messages[0].Role, "system")
	}
	if out.Messages[0].Content != "You are a pirate." {
		t.Errorf("system content = %v", out.Messages[0].Content)
	}
}

func TestTranslateSystemPromptArray(t *testing.T) {
	req := &AnthropicRequest{
		Model:     "test-model",
		MaxTokens: 100,
		System:    json.RawMessage(`[{"type":"text","text":"Part 1"},{"type":"text","text":"Part 2"}]`),
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if out.Messages[0].Content != "Part 1\nPart 2" {
		t.Errorf("system content = %v, want joined text", out.Messages[0].Content)
	}
}

func TestTranslateToolUseBlocks(t *testing.T) {
	req := &AnthropicRequest{
		Model:     "test-model",
		MaxTokens: 100,
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`"What's the weather?"`)},
			{Role: "assistant", Content: json.RawMessage(`[
				{"type":"text","text":"Let me check."},
				{"type":"tool_use","id":"toolu_123","name":"get_weather","input":{"city":"NYC"}}
			]`)},
			{Role: "user", Content: json.RawMessage(`[
				{"type":"tool_result","tool_use_id":"toolu_123","content":"72°F, sunny"}
			]`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// user, assistant (with tool_calls), tool
	if len(out.Messages) != 3 {
		t.Fatalf("messages count = %d, want 3", len(out.Messages))
	}

	assistant := out.Messages[1]
	if assistant.Content != "Let me check." {
		t.Errorf("assistant text = %v", assistant.Content)
	}
	if len(assistant.ToolCalls) != 1 {
		t.Fatalf("tool_calls count = %d, want 1", len(assistant.ToolCalls))
	}
	tc := assistant.ToolCalls[0]
	if tc.ID != "toolu_123" {
		t.Errorf("tool_call id = %q", tc.ID)
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("tool_call name = %q", tc.Function.Name)
	}
	// Arguments must be a JSON string
	var args map[string]interface{}
	if err := json.Unmarshal([]byte(tc.Function.Arguments), &args); err != nil {
		t.Errorf("arguments not valid JSON: %v", err)
	}

	tool := out.Messages[2]
	if tool.Role != "tool" {
		t.Errorf("tool role = %q", tool.Role)
	}
	if tool.ToolCallID != "toolu_123" {
		t.Errorf("tool_call_id = %q", tool.ToolCallID)
	}
}

func TestTranslateThinkingBlocksStripped(t *testing.T) {
	req := &AnthropicRequest{
		Model:     "test-model",
		MaxTokens: 100,
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
			{Role: "assistant", Content: json.RawMessage(`[
				{"type":"thinking","thinking":"internal thought process"},
				{"type":"text","text":"Hello!"}
			]`)},
			{Role: "user", Content: json.RawMessage(`"How are you?"`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	assistant := out.Messages[1]
	if assistant.Content != "Hello!" {
		t.Errorf("assistant should only have text, got content = %v", assistant.Content)
	}
	if len(assistant.ToolCalls) != 0 {
		t.Errorf("should have no tool_calls from thinking blocks")
	}
}

func TestTranslateImageBlock(t *testing.T) {
	req := &AnthropicRequest{
		Model:     "test-model",
		MaxTokens: 100,
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`[
				{"type":"image","source":{"type":"base64","media_type":"image/png","data":"iVBOR"}},
				{"type":"text","text":"What is this?"}
			]`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(out.Messages) != 1 {
		t.Fatalf("messages count = %d, want 1", len(out.Messages))
	}

	parts, ok := out.Messages[0].Content.([]OpenAIContentPart)
	if !ok {
		t.Fatalf("content should be []OpenAIContentPart, got %T", out.Messages[0].Content)
	}
	if len(parts) != 2 {
		t.Fatalf("parts count = %d, want 2", len(parts))
	}
	if parts[0].Type != "image_url" {
		t.Errorf("first part type = %q, want image_url", parts[0].Type)
	}
	if parts[0].ImageURL == nil || parts[0].ImageURL.URL != "data:image/png;base64,iVBOR" {
		t.Errorf("image URL = %v", parts[0].ImageURL)
	}
	if parts[1].Type != "text" {
		t.Errorf("second part type = %q, want text", parts[1].Type)
	}
}

func TestTranslateToolDefinitions(t *testing.T) {
	req := &AnthropicRequest{
		Model:     "test-model",
		MaxTokens: 100,
		Tools: []json.RawMessage{
			json.RawMessage(`{"name":"get_weather","description":"Get weather","input_schema":{"type":"object","properties":{"city":{"type":"string"}}}}`),
			json.RawMessage(`{"type":"bash_20250124","name":"bash_20250124"}`),
		},
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(out.Tools) != 2 {
		t.Fatalf("tools count = %d, want 2", len(out.Tools))
	}

	// Custom tool
	if out.Tools[0].Type != "function" {
		t.Errorf("tool type = %q, want function", out.Tools[0].Type)
	}
	if out.Tools[0].Function.Name != "get_weather" {
		t.Errorf("tool name = %q", out.Tools[0].Function.Name)
	}

	// Anthropic-defined tool translated to function
	if out.Tools[1].Function.Name != "bash_20250124" {
		t.Errorf("bash tool name = %q", out.Tools[1].Function.Name)
	}
}

func TestTranslateToolChoice(t *testing.T) {
	tests := []struct {
		input    AnthropicToolChoice
		expected interface{}
	}{
		{AnthropicToolChoice{Type: "auto"}, "auto"},
		{AnthropicToolChoice{Type: "any"}, "required"},
		{AnthropicToolChoice{Type: "none"}, "none"},
	}

	for _, tt := range tests {
		result := translateToolChoice(&tt.input)
		if result != tt.expected {
			t.Errorf("translateToolChoice(%q) = %v, want %v", tt.input.Type, result, tt.expected)
		}
	}
}

func TestTranslateStopReason(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"stop", "end_turn"},
		{"length", "max_tokens"},
		{"tool_calls", "tool_use"},
		{"content_filter", "end_turn"},
	}

	for _, tt := range tests {
		result := translateStopReason(tt.input)
		if result != tt.expected {
			t.Errorf("translateStopReason(%q) = %q, want %q", tt.input, result, tt.expected)
		}
	}
}

func TestTranslateResponseBasic(t *testing.T) {
	stop := "stop"
	content := "Hello there!"
	resp := &OpenAIResponse{
		ID:    "chatcmpl-123",
		Model: "qwen-8b",
		Choices: []OpenAIChoice{
			{
				Index: 0,
				Message: &OpenAIMessage{
					Role:    "assistant",
					Content: content,
				},
				FinishReason: &stop,
			},
		},
		Usage: &OpenAIUsage{
			PromptTokens:     10,
			CompletionTokens: 5,
			TotalTokens:      15,
		},
	}

	out, err := TranslateOpenAIToAnthropic(resp, "claude-sonnet-4-20250514")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if out.Type != "message" {
		t.Errorf("type = %q, want message", out.Type)
	}
	if out.Model != "claude-sonnet-4-20250514" {
		t.Errorf("model = %q, want claude-sonnet-4-20250514", out.Model)
	}
	if out.StopReason == nil || *out.StopReason != "end_turn" {
		t.Errorf("stop_reason = %v, want end_turn", out.StopReason)
	}
	if len(out.Content) != 1 || out.Content[0].Type != "text" {
		t.Fatalf("content should have one text block")
	}
	if out.Content[0].Text != "Hello there!" {
		t.Errorf("text = %q", out.Content[0].Text)
	}
	if out.Usage.InputTokens != 10 || out.Usage.OutputTokens != 5 {
		t.Errorf("usage = %+v", out.Usage)
	}
}

func TestTranslateResponseWithToolCalls(t *testing.T) {
	toolCalls := "tool_calls"
	resp := &OpenAIResponse{
		Choices: []OpenAIChoice{
			{
				Message: &OpenAIMessage{
					Role: "assistant",
					ToolCalls: []OpenAIToolCall{
						{
							ID:   "call_123",
							Type: "function",
							Function: OpenAIFunctionCall{
								Name:      "get_weather",
								Arguments: `{"city":"NYC"}`,
							},
						},
					},
				},
				FinishReason: &toolCalls,
			},
		},
	}

	out, err := TranslateOpenAIToAnthropic(resp, "test-model")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if out.StopReason == nil || *out.StopReason != "tool_use" {
		t.Errorf("stop_reason = %v, want tool_use", out.StopReason)
	}
	if len(out.Content) != 1 || out.Content[0].Type != "tool_use" {
		t.Fatalf("should have one tool_use block, got %d blocks", len(out.Content))
	}
	if out.Content[0].Name != "get_weather" {
		t.Errorf("tool name = %q", out.Content[0].Name)
	}
}

func TestTranslateStrippedRequestFields(t *testing.T) {
	req := &AnthropicRequest{
		Model:        "test-model",
		MaxTokens:    100,
		Thinking:     json.RawMessage(`{"type":"enabled","budget_tokens":1024}`),
		Metadata:     json.RawMessage(`{"user_id":"test"}`),
		ServiceTier:  json.RawMessage(`"auto"`),
		InferenceGeo: json.RawMessage(`"us"`),
		Container:    json.RawMessage(`{}`),
		OutputConfig: json.RawMessage(`{}`),
		Messages: []AnthropicMessage{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	out, err := TranslateAnthropicToOpenAI(req)
	if err != nil {
		t.Fatalf("should not error on stripped fields: %v", err)
	}

	// Should produce a valid request with none of the stripped fields
	if out.Model != "test-model" {
		t.Errorf("model = %q", out.Model)
	}
	if len(out.Messages) != 1 {
		t.Errorf("messages count = %d, want 1", len(out.Messages))
	}
}
