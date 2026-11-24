/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package auth

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strings"
)

type claimsKey struct{}

func ClaimsFromContext(ctx context.Context) *Claims {
	if c, ok := ctx.Value(claimsKey{}).(*Claims); ok {
		return c
	}
	return nil
}

type Middleware struct {
	jwt      *JWTValidator
	apiToken *APITokenValidator
}

func NewMiddleware(jwt *JWTValidator, apiToken *APITokenValidator) *Middleware {
	return &Middleware{jwt: jwt, apiToken: apiToken}
}

func (m *Middleware) Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token := extractToken(r)
		if token == "" {
			writeAuthError(w, r, http.StatusUnauthorized, "authentication_error", "Missing authentication token")
			return
		}

		var claims *Claims
		var err error

		if m.apiToken.IsAPIToken(token) {
			claims, err = m.apiToken.Validate(r.Context(), token)
		} else {
			claims, err = m.jwt.Validate(token)
		}

		// A token that could not be checked is a 503, which clients retry; a 401
		// tells them the token is wrong and to stop.
		if errors.Is(err, ErrAuthUnavailable) {
			slog.Warn("auth unavailable", "error", err)
			writeAuthError(w, r, http.StatusServiceUnavailable, "service_unavailable",
				"Authentication service unavailable, please retry")
			return
		}
		if err != nil {
			slog.Debug("auth failed", "error", err)
			writeAuthError(w, r, http.StatusUnauthorized, "authentication_error", "Invalid authentication token")
			return
		}

		ctx := context.WithValue(r.Context(), claimsKey{}, claims)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func extractToken(r *http.Request) string {
	if apiKey := r.Header.Get("x-api-key"); apiKey != "" {
		return apiKey
	}

	auth := r.Header.Get("Authorization")
	if auth == "" {
		return ""
	}

	parts := strings.SplitN(auth, " ", 2)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "bearer") {
		return ""
	}
	return parts[1]
}

func writeAuthError(w http.ResponseWriter, r *http.Request, status int, errorType, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)

	code := "invalid_token"
	if status == http.StatusServiceUnavailable {
		code = "auth_unavailable"
	}
	if strings.Contains(r.URL.Path, "/v1/messages") {
		w.Write([]byte(`{"type":"error","error":{"type":"` + errorType + `","message":"` + message + `"}}`))
	} else {
		w.Write([]byte(`{"error":{"message":"` + message + `","type":"` + errorType + `","code":"` + code + `"}}`))
	}
}
