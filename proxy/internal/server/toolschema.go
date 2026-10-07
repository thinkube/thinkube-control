/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"encoding/json"
	"strings"
)

// typeToolParameters gives every top-level tool parameter a plain "type"
// when its schema states one only through anyOf/oneOf branches or a type
// list, as Pydantic writes an optional field (list[str] | None becomes
// anyOf [{type: array}, {type: null}]).
//
// vLLM's XML tool parsers (qwen3_xml, qwen3_coder) convert each argument by
// properties[name]["type"], and read a missing type as "string": an optional
// list then reaches the client as the JSON text of the list instead of a
// list. The type is set when the branches that are not null agree on one;
// a $ref branch is resolved through the parameters' $defs or definitions.
// Parameters that already have a type, or whose branches disagree, are left
// as they are. The body is returned unchanged when nothing is set.
func typeToolParameters(body []byte) []byte {
	var req map[string]any
	if err := json.Unmarshal(body, &req); err != nil {
		return body
	}
	tools, ok := req["tools"].([]any)
	if !ok {
		return body
	}
	modified := false
	for _, t := range tools {
		tool, _ := t.(map[string]any)
		fn, _ := tool["function"].(map[string]any)
		params, _ := fn["parameters"].(map[string]any)
		props, _ := params["properties"].(map[string]any)
		defs := schemaDefs(params)
		for _, p := range props {
			prop, ok := p.(map[string]any)
			if !ok {
				continue
			}
			if typ := singleType(prop, defs); typ != "" {
				if current, ok := prop["type"].(string); ok && current == typ {
					continue
				}
				prop["type"] = typ
				modified = true
			}
		}
	}
	if !modified {
		return body
	}
	out, err := json.Marshal(req)
	if err != nil {
		return body
	}
	return out
}

// schemaDefs returns the definitions a $ref inside params can point at.
func schemaDefs(params map[string]any) map[string]any {
	for _, key := range []string{"$defs", "definitions"} {
		if defs, ok := params[key].(map[string]any); ok {
			return defs
		}
	}
	return nil
}

// singleType is the one non-null type a property's schema allows, or "" when
// it allows none or several, or one that cannot be read.
func singleType(prop map[string]any, defs map[string]any) string {
	switch t := prop["type"].(type) {
	case string:
		return t
	case []any:
		return oneOf(typesOf(t))
	}
	for _, key := range []string{"anyOf", "oneOf"} {
		branches, ok := prop[key].([]any)
		if !ok {
			continue
		}
		var types []string
		for _, b := range branches {
			branch, ok := b.(map[string]any)
			if !ok {
				return ""
			}
			typ := branchType(branch, defs)
			if typ == "" {
				return ""
			}
			types = append(types, typ)
		}
		return oneOf(types)
	}
	return ""
}

// branchType is the type of one anyOf/oneOf branch, following a local $ref.
func branchType(branch map[string]any, defs map[string]any) string {
	if t, ok := branch["type"].(string); ok {
		return t
	}
	if ref, ok := branch["$ref"].(string); ok {
		for _, prefix := range []string{"#/$defs/", "#/definitions/"} {
			if name, found := strings.CutPrefix(ref, prefix); found {
				if def, ok := defs[name].(map[string]any); ok {
					if t, ok := def["type"].(string); ok {
						return t
					}
				}
			}
		}
	}
	return ""
}

func typesOf(list []any) []string {
	var types []string
	for _, v := range list {
		if s, ok := v.(string); ok {
			types = append(types, s)
		}
	}
	return types
}

// oneOf is the single type left after dropping "null", or "".
func oneOf(types []string) string {
	found := ""
	for _, t := range types {
		if t == "null" {
			continue
		}
		if found != "" && found != t {
			return ""
		}
		found = t
	}
	return found
}
