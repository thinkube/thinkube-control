/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

func resolveErrorMessage(t *testing.T, model string, err error) (int, string) {
	t.Helper()
	w := httptest.NewRecorder()
	writeResolveError(w, "openai", model, err)
	var body openaiError
	if e := json.Unmarshal(w.Body.Bytes(), &body); e != nil {
		t.Fatalf("body is not an OpenAI error: %v", e)
	}
	return w.Code, body.Error.Message
}

// An alias whose model is mirrored but not loaded gets a 400, which clients do
// not retry, naming the alias, its model and what to do.
func TestResolveErrorNotLoadedNamesAliasAndLoad(t *testing.T) {
	code, msg := resolveErrorMessage(t, "thinkube-fast",
		&resolver.ModelError{Model: "nvidia/Qwen3.6-35B-A3B-NVFP4", Err: resolver.ErrModelNotLoaded})
	if code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", code)
	}
	want := "Model 'thinkube-fast' (nvidia/Qwen3.6-35B-A3B-NVFP4) is not loaded: load it in Thinkube Control, LLM Gateway"
	if msg != want {
		t.Fatalf("message = %q, want %q", msg, want)
	}
}

// A model the control plane does not have is a 404 that says to mirror it.
func TestResolveErrorNotFoundSaysMirror(t *testing.T) {
	code, msg := resolveErrorMessage(t, "org/m",
		&resolver.ModelError{Model: "org/m", Err: resolver.ErrModelNotFound})
	if code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", code)
	}
	want := "Model 'org/m' not found: mirror it in Thinkube Control, AI Models, then load it in LLM Gateway"
	if msg != want {
		t.Fatalf("message = %q, want %q", msg, want)
	}
}
