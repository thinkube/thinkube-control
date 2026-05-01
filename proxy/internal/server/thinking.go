package server

import "encoding/json"

// normalizeReasoning renames Ollama's "reasoning" field to "reasoning_content"
// in OpenAI chat completion responses for consistency across backends.
func normalizeReasoning(body []byte) []byte {
	var resp map[string]any
	if err := json.Unmarshal(body, &resp); err != nil {
		return body
	}

	choices, ok := resp["choices"].([]any)
	if !ok || len(choices) == 0 {
		return body
	}

	modified := false
	for _, c := range choices {
		choice, ok := c.(map[string]any)
		if !ok {
			continue
		}
		msg, ok := choice["message"].(map[string]any)
		if !ok {
			continue
		}
		if _, has := msg["reasoning_content"]; has {
			continue
		}
		reasoning, ok := msg["reasoning"].(string)
		if !ok || reasoning == "" {
			continue
		}
		msg["reasoning_content"] = reasoning
		delete(msg, "reasoning")
		modified = true
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
