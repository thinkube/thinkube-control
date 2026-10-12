/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import "testing"

// A backend constrained to a JSON schema writes the fields in the order of the
// schema's properties. Resolving an alias must leave that order, and every
// other byte of the request, as the client sent it.
func TestResolvingAnAliasKeepsTheSchemaOrder(t *testing.T) {
	body := `{"model": "thinkube-fast", "messages": [{"role": "user", "content": "x"}], "response_format": {"type": "json_schema", "json_schema": {"name": "n", "schema": {"type": "object", "properties": {"tokens": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "pos": {"type": "string"}, "lemma": {"type": "string"}, "feats": {"type": "string"}}}}, "entities": {"type": "array"}}}}}}`
	want := `{"model":"served/model","messages":[{"role": "user", "content": "x"}],"response_format":{"type": "json_schema", "json_schema": {"name": "n", "schema": {"type": "object", "properties": {"tokens": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "pos": {"type": "string"}, "lemma": {"type": "string"}, "feats": {"type": "string"}}}}, "entities": {"type": "array"}}}}}}`
	got, err := rewriteModelField([]byte(body), "served/model")
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != want {
		t.Fatalf("body:\n got %s\nwant %s", got, want)
	}
}

func TestAModelFieldMissingIsAddedAtTheEnd(t *testing.T) {
	got, err := rewriteModelField([]byte(`{"messages":[]}`), "m")
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != `{"messages":[],"model":"m"}` {
		t.Fatalf("body = %s", got)
	}
}

func TestResolvingAnAliasRefusesABodyThatIsNotAnObject(t *testing.T) {
	for _, body := range []string{`not json`, `[]`, `{"model": "a"} trailing`} {
		if _, err := rewriteModelField([]byte(body), "m"); err == nil {
			t.Fatalf("%s: no error", body)
		}
	}
}
