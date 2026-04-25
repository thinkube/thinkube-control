package protocol

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
)

func TranslateAnthropicToOpenAI(req *AnthropicRequest) (*OpenAIRequest, error) {
	out := &OpenAIRequest{
		Model:       req.Model,
		MaxTokens:   &req.MaxTokens,
		Temperature: req.Temperature,
		TopP:        req.TopP,
		Stream:      req.Stream,
	}

	if req.TopK != nil {
		slog.Debug("stripping top_k (not supported by OpenAI)", "top_k", *req.TopK)
	}

	if len(req.StopSequences) > 0 {
		out.Stop = req.StopSequences
	}

	// System prompt
	if len(req.System) > 0 {
		systemText, err := parseSystemPrompt(req.System)
		if err != nil {
			return nil, fmt.Errorf("parse system prompt: %w", err)
		}
		if systemText != "" {
			out.Messages = append(out.Messages, OpenAIMessage{
				Role:    "system",
				Content: systemText,
			})
		}
	}

	// Messages
	for _, msg := range req.Messages {
		translated, err := translateMessage(msg)
		if err != nil {
			return nil, fmt.Errorf("translate message: %w", err)
		}
		out.Messages = append(out.Messages, translated...)
	}

	// Tools
	for _, rawTool := range req.Tools {
		tool, err := translateTool(rawTool)
		if err != nil {
			slog.Warn("skipping untranslatable tool", "error", err)
			continue
		}
		out.Tools = append(out.Tools, *tool)
	}

	// Tool choice
	if req.ToolChoice != nil {
		out.ToolChoice = translateToolChoice(req.ToolChoice)
	}

	return out, nil
}

func parseSystemPrompt(raw json.RawMessage) (string, error) {
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s, nil
	}

	var blocks []struct {
		Type         string          `json:"type"`
		Text         string          `json:"text,omitempty"`
		CacheControl json.RawMessage `json:"cache_control,omitempty"`
	}
	if err := json.Unmarshal(raw, &blocks); err != nil {
		return "", fmt.Errorf("system must be string or array: %w", err)
	}

	var parts []string
	for _, b := range blocks {
		if b.Type == "text" && b.Text != "" {
			parts = append(parts, b.Text)
		}
	}
	return strings.Join(parts, "\n"), nil
}

func translateMessage(msg AnthropicMessage) ([]OpenAIMessage, error) {
	// Try string content first
	var strContent string
	if err := json.Unmarshal(msg.Content, &strContent); err == nil {
		return []OpenAIMessage{{Role: msg.Role, Content: strContent}}, nil
	}

	// Parse as array of content blocks
	var blocks []ContentBlock
	if err := json.Unmarshal(msg.Content, &blocks); err != nil {
		return nil, fmt.Errorf("content must be string or array: %w", err)
	}

	if msg.Role == "assistant" {
		return translateAssistantBlocks(blocks)
	}

	return translateUserBlocks(blocks, msg.Role)
}

func translateAssistantBlocks(blocks []ContentBlock) ([]OpenAIMessage, error) {
	var textParts []string
	var toolCalls []OpenAIToolCall

	for _, b := range blocks {
		switch b.Type {
		case "text":
			textParts = append(textParts, b.Text)
		case "tool_use":
			args, err := json.Marshal(json.RawMessage(b.Input))
			if err != nil {
				args = b.Input
			}
			toolCalls = append(toolCalls, OpenAIToolCall{
				ID:   b.ID,
				Type: "function",
				Function: OpenAIFunctionCall{
					Name:      b.Name,
					Arguments: string(args),
				},
			})
		case "thinking", "redacted_thinking":
			// Strip silently
		case "server_tool_use":
			args, _ := json.Marshal(json.RawMessage(b.Input))
			toolCalls = append(toolCalls, OpenAIToolCall{
				ID:   b.ID,
				Type: "function",
				Function: OpenAIFunctionCall{
					Name:      b.Name,
					Arguments: string(args),
				},
			})
		default:
			slog.Debug("stripping unknown assistant content block type", "type", b.Type)
		}
	}

	msg := OpenAIMessage{Role: "assistant"}
	if len(textParts) > 0 {
		msg.Content = strings.Join(textParts, "")
	}
	if len(toolCalls) > 0 {
		msg.ToolCalls = toolCalls
	}

	return []OpenAIMessage{msg}, nil
}

func translateUserBlocks(blocks []ContentBlock, role string) ([]OpenAIMessage, error) {
	var msgs []OpenAIMessage
	var contentParts []OpenAIContentPart
	hasToolResults := false

	for _, b := range blocks {
		switch b.Type {
		case "text":
			contentParts = append(contentParts, OpenAIContentPart{
				Type: "text",
				Text: b.Text,
			})

		case "image":
			part, err := translateImageBlock(b)
			if err != nil {
				slog.Warn("skipping untranslatable image block", "error", err)
				continue
			}
			contentParts = append(contentParts, *part)

		case "document":
			part, err := translateDocumentBlock(b)
			if err != nil {
				slog.Warn("skipping untranslatable document block", "error", err)
				continue
			}
			contentParts = append(contentParts, *part)

		case "tool_result":
			hasToolResults = true
			toolContent := extractToolResultContent(b)
			msgs = append(msgs, OpenAIMessage{
				Role:       "tool",
				Content:    toolContent,
				ToolCallID: b.ToolUseID,
			})

		case "web_search_tool_result", "code_execution_tool_result",
			"bash_code_execution_tool_result", "text_editor_code_execution_tool_result",
			"tool_search_tool_result":
			hasToolResults = true
			toolContent := extractServerToolResultContent(b)
			msgs = append(msgs, OpenAIMessage{
				Role:       "tool",
				Content:    toolContent,
				ToolCallID: b.ID,
			})

		case "thinking", "redacted_thinking":
			// Strip silently

		case "container_upload", "tool_reference":
			// Strip silently

		default:
			slog.Debug("stripping unknown user content block type", "type", b.Type)
		}
	}

	if len(contentParts) > 0 {
		var result []OpenAIMessage
		if len(contentParts) == 1 && contentParts[0].Type == "text" {
			result = append(result, OpenAIMessage{
				Role:    role,
				Content: contentParts[0].Text,
			})
		} else {
			result = append(result, OpenAIMessage{
				Role:    role,
				Content: contentParts,
			})
		}
		return append(result, msgs...), nil
	}

	if hasToolResults {
		return msgs, nil
	}

	return []OpenAIMessage{{Role: role, Content: ""}}, nil
}

func translateImageBlock(b ContentBlock) (*OpenAIContentPart, error) {
	if len(b.Source) == 0 {
		return nil, fmt.Errorf("image block missing source")
	}

	var src ImageSource
	if err := json.Unmarshal(b.Source, &src); err != nil {
		return nil, fmt.Errorf("parse image source: %w", err)
	}

	var url string
	switch src.Type {
	case "base64":
		mediaType := src.MediaType
		if mediaType == "" {
			mediaType = "image/png"
		}
		url = fmt.Sprintf("data:%s;base64,%s", mediaType, src.Data)
	case "url":
		url = src.URL
	default:
		return nil, fmt.Errorf("unsupported image source type: %s", src.Type)
	}

	return &OpenAIContentPart{
		Type:     "image_url",
		ImageURL: &OpenAIImage{URL: url},
	}, nil
}

func translateDocumentBlock(b ContentBlock) (*OpenAIContentPart, error) {
	if len(b.Source) == 0 {
		return nil, fmt.Errorf("document block missing source")
	}

	var src DocumentSource
	if err := json.Unmarshal(b.Source, &src); err != nil {
		return nil, fmt.Errorf("parse document source: %w", err)
	}

	switch src.Type {
	case "text":
		return &OpenAIContentPart{
			Type: "text",
			Text: src.Data,
		}, nil
	case "base64":
		label := src.Filename
		if label == "" {
			label = "document"
		}
		return &OpenAIContentPart{
			Type: "text",
			Text: fmt.Sprintf("[Document: %s, type: %s — binary documents not supported by backend]", label, src.MediaType),
		}, nil
	default:
		return nil, fmt.Errorf("unsupported document source type: %s", src.Type)
	}
}

func extractToolResultContent(b ContentBlock) string {
	if len(b.ToolContent) == 0 {
		return ""
	}

	// Try string first
	var s string
	if err := json.Unmarshal(b.ToolContent, &s); err == nil {
		return s
	}

	// Try array of content blocks
	var blocks []struct {
		Type string `json:"type"`
		Text string `json:"text,omitempty"`
	}
	if err := json.Unmarshal(b.ToolContent, &blocks); err == nil {
		var parts []string
		for _, bl := range blocks {
			if bl.Type == "text" && bl.Text != "" {
				parts = append(parts, bl.Text)
			}
		}
		if len(parts) > 0 {
			return strings.Join(parts, "\n")
		}
	}

	return string(b.ToolContent)
}

func extractServerToolResultContent(b ContentBlock) string {
	if len(b.Input) > 0 {
		return string(b.Input)
	}
	if b.Text != "" {
		return b.Text
	}
	return ""
}

func translateTool(raw json.RawMessage) (*OpenAITool, error) {
	var def AnthropicToolDef
	if err := json.Unmarshal(raw, &def); err != nil {
		return nil, fmt.Errorf("parse tool: %w", err)
	}

	// All tool types get translated to OpenAI function format
	// Anthropic-defined tools (bash_20250124, text_editor_20250124, computer_20250124, etc.)
	// are client-side tools; the backend needs to know they exist for the tool use flow
	return &OpenAITool{
		Type: "function",
		Function: OpenAIFunction{
			Name:        def.Name,
			Description: def.Description,
			Parameters:  def.InputSchema,
		},
	}, nil
}

func translateToolChoice(tc *AnthropicToolChoice) interface{} {
	switch tc.Type {
	case "auto":
		return "auto"
	case "any":
		return "required"
	case "none":
		return "none"
	case "tool":
		return map[string]interface{}{
			"type":     "function",
			"function": map[string]string{"name": tc.Name},
		}
	default:
		return "auto"
	}
}

// TranslateOpenAIToAnthropic converts an OpenAI response to Anthropic format
func TranslateOpenAIToAnthropic(resp *OpenAIResponse, model string) (*AnthropicResponse, error) {
	if len(resp.Choices) == 0 {
		return nil, fmt.Errorf("no choices in response")
	}

	choice := resp.Choices[0]
	out := &AnthropicResponse{
		ID:    generateMessageID(),
		Type:  "message",
		Role:  "assistant",
		Model: model,
	}

	// Stop reason
	if choice.FinishReason != nil {
		reason := translateStopReason(*choice.FinishReason)
		out.StopReason = &reason
	}

	// Content
	if choice.Message != nil {
		// Text content
		if choice.Message.Content != nil {
			if textStr, ok := choice.Message.Content.(string); ok && textStr != "" {
				out.Content = append(out.Content, ResponseBlock{
					Type: "text",
					Text: textStr,
				})
			}
		}

		// Tool calls
		for _, tc := range choice.Message.ToolCalls {
			var input json.RawMessage
			if tc.Function.Arguments != "" {
				input = json.RawMessage(tc.Function.Arguments)
			} else {
				input = json.RawMessage("{}")
			}
			out.Content = append(out.Content, ResponseBlock{
				Type:  "tool_use",
				ID:    tc.ID,
				Name:  tc.Function.Name,
				Input: input,
			})
		}
	}

	if len(out.Content) == 0 {
		out.Content = []ResponseBlock{}
	}

	// Usage
	if resp.Usage != nil {
		out.Usage = AnthropicUsage{
			InputTokens:  resp.Usage.PromptTokens,
			OutputTokens: resp.Usage.CompletionTokens,
		}
	}

	return out, nil
}

func translateStopReason(reason string) string {
	switch reason {
	case "stop":
		return "end_turn"
	case "length":
		return "max_tokens"
	case "tool_calls":
		return "tool_use"
	case "content_filter":
		return "end_turn"
	default:
		return "end_turn"
	}
}

func generateMessageID() string {
	b := make([]byte, 12)
	rand.Read(b)
	return "msg_" + hex.EncodeToString(b)
}
