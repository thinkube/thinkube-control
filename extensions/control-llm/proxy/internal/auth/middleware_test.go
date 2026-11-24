/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package auth

import (
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/golang-jwt/jwt/v5"
)

type authErrorBody struct {
	Error struct {
		Message string `json:"message"`
		Type    string `json:"type"`
		Code    string `json:"code"`
	} `json:"error"`
}

// backend serves /api/v1/tokens/verify with the given status and body.
func backend(t *testing.T, status int, body string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(status)
		w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv
}

// unreachable is a URL nothing listens on.
func unreachable(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.NotFoundHandler())
	url := srv.URL
	srv.Close()
	return url
}

func call(t *testing.T, backendURL, keycloakURL, token string) (int, authErrorBody, bool) {
	t.Helper()
	reached := false
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	})
	m := NewMiddleware(NewJWTValidator(keycloakURL, "thinkube", "llm-proxy"), NewAPITokenValidator(backendURL))
	req := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	m.Wrap(next).ServeHTTP(w, req)
	var body authErrorBody
	if !reached {
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatalf("body is not an OpenAI error: %v: %s", err, w.Body.String())
		}
	}
	return w.Code, body, reached
}

func TestAPITokenAcceptedByBackendPasses(t *testing.T) {
	srv := backend(t, http.StatusOK, `{"valid":true,"username":"thinkube"}`)
	code, _, reached := call(t, srv.URL, unreachable(t), "tk_good")
	if !reached || code != http.StatusOK {
		t.Fatalf("status = %d, reached = %v; want 200 and the handler reached", code, reached)
	}
}

func TestAPITokenRejectedByBackendIs401(t *testing.T) {
	srv := backend(t, http.StatusUnauthorized, `{"detail":"Not authenticated"}`)
	code, body, reached := call(t, srv.URL, unreachable(t), "tk_bad")
	if reached || code != http.StatusUnauthorized || body.Error.Code != "invalid_token" {
		t.Fatalf("status = %d, code = %q; want 401 invalid_token", code, body.Error.Code)
	}
}

func TestAPITokenMarkedInvalidByBackendIs401(t *testing.T) {
	srv := backend(t, http.StatusOK, `{"valid":false}`)
	code, body, _ := call(t, srv.URL, unreachable(t), "tk_bad")
	if code != http.StatusUnauthorized || body.Error.Code != "invalid_token" {
		t.Fatalf("status = %d, code = %q; want 401 invalid_token", code, body.Error.Code)
	}
}

// A backend that fails or cannot be reached says nothing about the token: the
// client gets a 503 to retry, not a 401 that tells it to stop.
func TestAPITokenWithBackendErrorIs503(t *testing.T) {
	srv := backend(t, http.StatusInternalServerError, `Internal Server Error`)
	code, body, _ := call(t, srv.URL, unreachable(t), "tk_good")
	if code != http.StatusServiceUnavailable || body.Error.Code != "auth_unavailable" {
		t.Fatalf("status = %d, code = %q; want 503 auth_unavailable", code, body.Error.Code)
	}
}

func TestAPITokenWithBackendUnreachableIs503(t *testing.T) {
	code, body, _ := call(t, unreachable(t), unreachable(t), "tk_good")
	if code != http.StatusServiceUnavailable || body.Error.Code != "auth_unavailable" {
		t.Fatalf("status = %d, code = %q; want 503 auth_unavailable", code, body.Error.Code)
	}
}

func TestAPITokenWithUnreadableBackendAnswerIs503(t *testing.T) {
	srv := backend(t, http.StatusOK, `<html>`)
	code, body, _ := call(t, srv.URL, unreachable(t), "tk_good")
	if code != http.StatusServiceUnavailable || body.Error.Code != "auth_unavailable" {
		t.Fatalf("status = %d, code = %q; want 503 auth_unavailable", code, body.Error.Code)
	}
}

func TestJWTWithKeycloakUnreachableIs503(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	keycloak := unreachable(t)
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, jwt.MapClaims{"iss": keycloak + "/realms/thinkube", "sub": "u"})
	tok.Header["kid"] = "k1"
	signed, err := tok.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	code, body, _ := call(t, unreachable(t), keycloak, signed)
	if code != http.StatusServiceUnavailable || body.Error.Code != "auth_unavailable" {
		t.Fatalf("status = %d, code = %q; want 503 auth_unavailable", code, body.Error.Code)
	}
}

func TestMissingTokenIs401(t *testing.T) {
	m := NewMiddleware(NewJWTValidator(unreachable(t), "thinkube", "llm-proxy"), NewAPITokenValidator(unreachable(t)))
	w := httptest.NewRecorder()
	m.Wrap(http.NotFoundHandler()).ServeHTTP(w, httptest.NewRequest("POST", "/v1/chat/completions", nil))
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", w.Code)
	}
}
