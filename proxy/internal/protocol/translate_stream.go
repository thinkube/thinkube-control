package protocol

import (
	"encoding/json"
	"fmt"
	"strings"
)

type StreamTranslator struct {
	model       string
	messageID   string
	blockIndex  int
	hasTextBlock bool
	toolArgs    map[int]string // index -> accumulated arguments
	toolNames   map[int]string // index -> function name
	toolIDs     map[int]string // index -> tool call id
	stopReason  string
	outputTokens int
	started     bool
	firstContent bool
}

func NewStreamTranslator(model string) *StreamTranslator {
	return &StreamTranslator{
		model:     model,
		messageID: generateMessageID(),
		toolArgs:  make(map[int]string),
		toolNames: make(map[int]string),
		toolIDs:   make(map[int]string),
	}
}

func (st *StreamTranslator) TranslateChunk(chunk *OpenAIStreamChunk) ([]json.RawMessage, error) {
	if chunk == nil || len(chunk.Choices) == 0 {
		if chunk != nil && chunk.Usage != nil {
			st.outputTokens = chunk.Usage.CompletionTokens
		}
		return nil, nil
	}

	if chunk.Usage != nil {
		st.outputTokens = chunk.Usage.CompletionTokens
	}

	choice := chunk.Choices[0]
	var events []json.RawMessage

	// Emit message_start on first chunk
	if !st.started {
		st.started = true
		startEvt := MessageStartEvent{
			Type: "message_start",
			Message: AnthropicResponse{
				ID:      st.messageID,
				Type:    "message",
				Role:    "assistant",
				Model:   st.model,
				Content: []ResponseBlock{},
				Usage:   AnthropicUsage{InputTokens: 0, OutputTokens: 0},
			},
		}
		events = append(events, mustMarshal(startEvt))

		pingEvt := PingEvent{Type: "ping"}
		events = append(events, mustMarshal(pingEvt))
	}

	// Content deltas
	if choice.Delta.Content != nil && *choice.Delta.Content != "" {
		text := *choice.Delta.Content

		if !st.firstContent {
			st.firstContent = true
			st.hasTextBlock = true

			events = append(events, mustMarshal(ContentBlockStartEvent{
				Type:  "content_block_start",
				Index: st.blockIndex,
				ContentBlock: ResponseBlock{
					Type: "text",
					Text: "",
				},
			}))
		}

		events = append(events, mustMarshal(ContentBlockDeltaEvent{
			Type:  "content_block_delta",
			Index: st.blockIndex,
			Delta: DeltaBlock{
				Type: "text_delta",
				Text: text,
			},
		}))
	}

	// Tool call deltas
	for _, tc := range choice.Delta.ToolCalls {
		idx := tc.Index

		if tc.ID != "" {
			// New tool call starting
			if st.hasTextBlock {
				events = append(events, mustMarshal(ContentBlockStopEvent{
					Type:  "content_block_stop",
					Index: st.blockIndex,
				}))
				st.blockIndex++
				st.hasTextBlock = false
			} else if _, exists := st.toolIDs[idx]; !exists && st.blockIndex > 0 {
				// Close previous tool block if starting a new one
				if idx > 0 {
					events = append(events, mustMarshal(ContentBlockStopEvent{
						Type:  "content_block_stop",
						Index: st.blockIndex,
					}))
					st.blockIndex++
				}
			}

			st.toolIDs[idx] = tc.ID
			st.toolNames[idx] = tc.Function.Name
			st.toolArgs[idx] = ""

			events = append(events, mustMarshal(ContentBlockStartEvent{
				Type:  "content_block_start",
				Index: st.blockIndex,
				ContentBlock: ResponseBlock{
					Type:  "tool_use",
					ID:    tc.ID,
					Name:  tc.Function.Name,
					Input: json.RawMessage("{}"),
				},
			}))
		}

		if tc.Function.Arguments != "" {
			st.toolArgs[idx] += tc.Function.Arguments

			events = append(events, mustMarshal(ContentBlockDeltaEvent{
				Type:  "content_block_delta",
				Index: st.blockIndex,
				Delta: DeltaBlock{
					Type:        "input_json_delta",
					PartialJSON: tc.Function.Arguments,
				},
			}))
		}
	}

	// Finish reason
	if choice.FinishReason != nil {
		st.stopReason = translateStopReason(*choice.FinishReason)
	}

	return events, nil
}

// Finish emits the closing events (content_block_stop, message_delta, message_stop)
func (st *StreamTranslator) Finish() []json.RawMessage {
	var events []json.RawMessage

	if !st.started {
		return events
	}

	// Close last open block
	if st.firstContent || len(st.toolIDs) > 0 {
		events = append(events, mustMarshal(ContentBlockStopEvent{
			Type:  "content_block_stop",
			Index: st.blockIndex,
		}))
	}

	stopReason := st.stopReason
	if stopReason == "" {
		stopReason = "end_turn"
	}

	events = append(events, mustMarshal(MessageDeltaEvent{
		Type: "message_delta",
		Delta: MessageDelta{
			StopReason: &stopReason,
		},
		Usage: &DeltaUsage{
			OutputTokens: st.outputTokens,
		},
	}))

	events = append(events, mustMarshal(MessageStopEvent{
		Type: "message_stop",
	}))

	return events
}

// FormatSSE formats a JSON event as an Anthropic SSE line
func FormatSSE(eventType string, data json.RawMessage) string {
	return fmt.Sprintf("event: %s\ndata: %s\n\n", eventType, string(data))
}

// ExtractEventType extracts the "type" field from a JSON event
func ExtractEventType(data json.RawMessage) string {
	var typed struct {
		Type string `json:"type"`
	}
	json.Unmarshal(data, &typed)

	switch {
	case strings.HasPrefix(typed.Type, "message_start"):
		return "message_start"
	case strings.HasPrefix(typed.Type, "content_block_start"):
		return "content_block_start"
	case strings.HasPrefix(typed.Type, "content_block_delta"):
		return "content_block_delta"
	case strings.HasPrefix(typed.Type, "content_block_stop"):
		return "content_block_stop"
	case strings.HasPrefix(typed.Type, "message_delta"):
		return "message_delta"
	case strings.HasPrefix(typed.Type, "message_stop"):
		return "message_stop"
	case typed.Type == "ping":
		return "ping"
	default:
		return typed.Type
	}
}

func mustMarshal(v interface{}) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}
