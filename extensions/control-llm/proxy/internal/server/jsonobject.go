/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// object is a JSON object that keeps its keys in the order they were written
// and each value as the bytes that were sent. A request is rewritten through
// it rather than through map[string]any, whose encoding sorts the keys: a
// backend that constrains its output to a JSON schema writes the fields in
// the order of the schema's properties, so a sorted schema changes what the
// model decides first.
type object []field

type field struct {
	key   string
	value json.RawMessage
}

func decodeObject(data []byte) (object, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	if delim, ok := tok.(json.Delim); !ok || delim != '{' {
		return nil, fmt.Errorf("not a JSON object")
	}
	obj := object{}
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil, err
		}
		key, ok := tok.(string)
		if !ok {
			return nil, fmt.Errorf("object key is not a string")
		}
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return nil, err
		}
		obj = append(obj, field{key, value})
	}
	if _, err := dec.Token(); err != nil {
		return nil, err
	}
	if len(bytes.TrimSpace(data[dec.InputOffset():])) > 0 {
		return nil, fmt.Errorf("data after the JSON object")
	}
	return obj, nil
}

func (o object) get(key string) (json.RawMessage, bool) {
	for _, f := range o {
		if f.key == key {
			return f.value, true
		}
	}
	return nil, false
}

// set replaces the value of key where it is, or adds key at the end.
func (o object) set(key string, value json.RawMessage) object {
	for i, f := range o {
		if f.key == key {
			o[i].value = value
			return o
		}
	}
	return append(o, field{key, value})
}

func (o object) encode() []byte {
	var buf bytes.Buffer
	buf.WriteByte('{')
	for i, f := range o {
		if i > 0 {
			buf.WriteByte(',')
		}
		key, _ := json.Marshal(f.key) // a string always encodes
		buf.Write(key)
		buf.WriteByte(':')
		buf.Write(f.value)
	}
	buf.WriteByte('}')
	return buf.Bytes()
}

func encodeArray(items []json.RawMessage) []byte {
	var buf bytes.Buffer
	buf.WriteByte('[')
	for i, item := range items {
		if i > 0 {
			buf.WriteByte(',')
		}
		buf.Write(item)
	}
	buf.WriteByte(']')
	return buf.Bytes()
}
