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
// as they are. The body is returned unchanged when nothing is set; when a
// type is set, only that property changes: every other key keeps its bytes
// and its place.
func typeToolParameters(body []byte) ([]byte, error) {
	req, err := decodeObject(body)
	if err != nil {
		return nil, err
	}
	rawTools, ok := req.get("tools")
	if !ok {
		return body, nil
	}
	var tools []json.RawMessage
	if err := json.Unmarshal(rawTools, &tools); err != nil {
		return body, nil // "tools" that is not a list is the backend's to refuse
	}
	modified := false
	for i, tool := range tools {
		if typed, ok := editObject(tool, []string{"function", "parameters"}, typeProperties); ok {
			tools[i] = typed
			modified = true
		}
	}
	if !modified {
		return body, nil
	}
	return req.set("tools", encodeArray(tools)).encode(), nil
}

// editObject applies edit to the object found at path inside raw, and
// returns raw with that object replaced, or false when nothing changed or
// the path does not lead to an object.
func editObject(raw json.RawMessage, path []string, edit func(object) bool) (json.RawMessage, bool) {
	obj, err := decodeObject(raw)
	if err != nil {
		return raw, false
	}
	if len(path) == 0 {
		if !edit(obj) {
			return raw, false
		}
		return obj.encode(), true
	}
	child, ok := obj.get(path[0])
	if !ok {
		return raw, false
	}
	edited, ok := editObject(child, path[1:], edit)
	if !ok {
		return raw, false
	}
	return obj.set(path[0], edited).encode(), true
}

// typeProperties sets the type of each property of a tool's parameters that
// singleType can name, in place.
func typeProperties(params object) bool {
	var schema map[string]any
	if err := json.Unmarshal(params.encode(), &schema); err != nil {
		return false
	}
	defs := schemaDefs(schema)
	rawProps, ok := params.get("properties")
	if !ok {
		return false
	}
	props, err := decodeObject(rawProps)
	if err != nil {
		return false
	}
	modified := false
	for i, p := range props {
		var prop map[string]any
		if err := json.Unmarshal(p.value, &prop); err != nil {
			continue
		}
		typ := singleType(prop, defs)
		if typ == "" {
			continue
		}
		if current, ok := prop["type"].(string); ok && current == typ {
			continue
		}
		propObj, err := decodeObject(p.value)
		if err != nil {
			continue
		}
		typeName, _ := json.Marshal(typ) // a string always encodes
		props[i].value = propObj.set("type", typeName).encode()
		modified = true
	}
	if modified {
		params.set("properties", props.encode())
	}
	return modified
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
