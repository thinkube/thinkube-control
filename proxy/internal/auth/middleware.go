package auth

import (
	"context"
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
			writeAuthError(w, r, http.StatusUnauthorized, "Missing authentication token")
			return
		}

		var claims *Claims
		var err error

		if m.apiToken.IsAPIToken(token) {
			claims, err = m.apiToken.Validate(r.Context(), token)
		} else {
			claims, err = m.jwt.Validate(token)
		}

		if err != nil {
			slog.Debug("auth failed", "error", err)
			writeAuthError(w, r, http.StatusUnauthorized, "Invalid authentication token")
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

func writeAuthError(w http.ResponseWriter, r *http.Request, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)

	if strings.Contains(r.URL.Path, "/v1/messages") {
		w.Write([]byte(`{"type":"error","error":{"type":"authentication_error","message":"` + message + `"}}`))
	} else {
		w.Write([]byte(`{"error":{"message":"` + message + `","type":"authentication_error","code":"invalid_token"}}`))
	}
}
