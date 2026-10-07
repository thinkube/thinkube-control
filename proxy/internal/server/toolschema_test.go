/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"encoding/json"
	"testing"
)

// paramTypes runs typeToolParameters on a request with one tool of the given
// parameters and returns each property's "type" after it.
func paramTypes(t *testing.T, params string) map[string]any {
	t.Helper()
	body := []byte(`{"model": "m", "tools": [{"type": "function", "function": {"name": "f", "parameters": ` + params + `}}]}`)
	var req struct {
		Tools []struct {
			Function struct {
				Parameters struct {
					Properties map[string]map[string]any `json:"properties"`
				} `json:"parameters"`
			} `json:"function"`
		} `json:"tools"`
	}
	if err := json.Unmarshal(typeToolParameters(body), &req); err != nil {
		t.Fatal(err)
	}
	types := map[string]any{}
	for name, prop := range req.Tools[0].Function.Parameters.Properties {
		types[name] = prop["type"]
	}
	return types
}

func TestAnOptionalListGetsTheArrayType(t *testing.T) {
	types := paramTypes(t, `{"type": "object", "properties": {
		"status": {"anyOf": [{"type": "array", "items": {"$ref": "#/$defs/S"}}, {"type": "null"}]}},
		"$defs": {"S": {"type": "string", "enum": ["sent", "accepted"]}}}`)
	if types["status"] != "array" {
		t.Fatalf("status type = %v, want array", types["status"])
	}
}

func TestARefBranchIsResolvedThroughDefs(t *testing.T) {
	types := paramTypes(t, `{"type": "object", "properties": {
		"filter": {"anyOf": [{"$ref": "#/$defs/F"}, {"type": "null"}]}},
		"$defs": {"F": {"type": "object", "properties": {}}}}`)
	if types["filter"] != "object" {
		t.Fatalf("filter type = %v, want object", types["filter"])
	}
}

func TestATypeListIsReducedToItsNonNullType(t *testing.T) {
	types := paramTypes(t, `{"type": "object", "properties": {"limit": {"type": ["integer", "null"]}}}`)
	if types["limit"] != "integer" {
		t.Fatalf("limit type = %v, want integer", types["limit"])
	}
}

func TestBranchesThatDisagreeAndPlainTypesAreLeftAlone(t *testing.T) {
	types := paramTypes(t, `{"type": "object", "properties": {
		"either": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
		"unknown": {"anyOf": [{"$ref": "#/$defs/Missing"}, {"type": "null"}]},
		"query": {"type": "string"}}}`)
	if _, set := types["either"]; set && types["either"] != nil {
		t.Fatalf("either type = %v, want none", types["either"])
	}
	if types["unknown"] != nil {
		t.Fatalf("unknown type = %v, want none", types["unknown"])
	}
	if types["query"] != "string" {
		t.Fatalf("query type = %v, want string", types["query"])
	}
}

func TestABodyWithNothingToTypeIsReturnedAsItWas(t *testing.T) {
	for _, body := range []string{
		`{"model": "m", "messages": []}`,
		`{"model": "m", "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]}`,
		`not json`,
	} {
		if got := string(typeToolParameters([]byte(body))); got != body {
			t.Fatalf("body changed:\n got %s\nwant %s", got, body)
		}
	}
}
