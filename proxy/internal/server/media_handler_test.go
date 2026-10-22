/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package server

import (
	"bytes"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/thinkube/thinkube-control/proxy/internal/forwarder"
	"github.com/thinkube/thinkube-control/proxy/internal/resolver"
)

// mediaBackend is the control plane and the model server in one: it resolves
// every name to the model org/media, and answers each media route with what
// it received, so a test sees the request the model server got.
func mediaBackend(t *testing.T) *httptest.Server {
	t.Helper()
	var srv *httptest.Server
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/llm/models/resolve", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(resolver.ResolveResult{
			BackendURL: srv.URL, APIPath: "/v1",
			ModelID: "org/media", ServingName: "org/media",
			ModelState: "available",
		})
	})
	echo := func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("X-Got-Content-Type", r.Header.Get("Content-Type"))
		io.Copy(w, r.Body)
	}
	mux.HandleFunc("/v1/images/generations", echo)
	mux.HandleFunc("/v1/audio/speech", echo)
	mux.HandleFunc("/v1/audio/transcriptions", echo)
	srv = httptest.NewServer(mux)
	return srv
}

func mediaHandler(backendURL string) *OpenAIHandler {
	return NewOpenAIHandler(resolver.New(backendURL), forwarder.New(5*time.Second), 1<<20)
}

func TestAnImageRequestReachesItsModelUnderTheServedName(t *testing.T) {
	backend := mediaBackend(t)
	defer backend.Close()

	rec := httptest.NewRecorder()
	mediaHandler(backend.URL).ImageGenerations(rec, httptest.NewRequest("POST", "/v1/images/generations",
		strings.NewReader(`{"model":"an-alias","prompt":"a lighthouse"}`)))

	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	var got map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("the model server got no JSON: %s", rec.Body.String())
	}
	if got["model"] != "org/media" || got["prompt"] != "a lighthouse" {
		t.Fatalf("the model server got %v", got)
	}
}

func TestSpeechAudioIsCopiedBackUnchanged(t *testing.T) {
	backend := mediaBackend(t)
	defer backend.Close()

	request := `{"model":"org/media","input":"hello","voice":"a"}`
	rec := httptest.NewRecorder()
	mediaHandler(backend.URL).AudioSpeech(rec, httptest.NewRequest("POST", "/v1/audio/speech", strings.NewReader(request)))

	if rec.Code != http.StatusOK || rec.Body.String() != request {
		t.Fatalf("status %d, body %q", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("Content-Type") != "application/octet-stream" {
		t.Fatalf("content type %q", rec.Header().Get("Content-Type"))
	}
}

func TestATranscriptionFormReachesItsModelWithTheFileAndTheServedName(t *testing.T) {
	backend := mediaBackend(t)
	defer backend.Close()

	var form bytes.Buffer
	writer := multipart.NewWriter(&form)
	writer.WriteField("model", "an-alias")
	file, _ := writer.CreateFormFile("file", "speech.wav")
	file.Write([]byte("RIFF-audio-bytes"))
	writer.WriteField("language", "en")
	writer.Close()

	req := httptest.NewRequest("POST", "/v1/audio/transcriptions", &form)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	rec := httptest.NewRecorder()
	mediaHandler(backend.URL).AudioTranscriptions(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	_, params, err := mime.ParseMediaType(rec.Header().Get("X-Got-Content-Type"))
	if err != nil || params["boundary"] == "" {
		t.Fatalf("the model server got content type %q", rec.Header().Get("X-Got-Content-Type"))
	}
	fields := map[string]string{}
	reader := multipart.NewReader(rec.Body, params["boundary"])
	for {
		p, err := reader.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatalf("the model server got no valid form: %v", err)
		}
		data, _ := io.ReadAll(p)
		fields[p.FormName()] = string(data)
	}
	if fields["model"] != "org/media" || fields["file"] != "RIFF-audio-bytes" || fields["language"] != "en" {
		t.Fatalf("the model server got %v", fields)
	}
}

func TestATranscriptionThatIsNotAFormIsRefused(t *testing.T) {
	backend := mediaBackend(t)
	defer backend.Close()

	rec := httptest.NewRecorder()
	mediaHandler(backend.URL).AudioTranscriptions(rec, httptest.NewRequest("POST", "/v1/audio/transcriptions",
		strings.NewReader(`{"model":"org/media"}`)))

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
}
