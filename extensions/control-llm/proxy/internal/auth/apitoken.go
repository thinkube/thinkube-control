/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"
)

// ErrAuthUnavailable marks a token that could not be checked: the backend or
// Keycloak did not answer, or answered with an error. It is not a rejection.
var ErrAuthUnavailable = errors.New("authentication service unavailable")

type tokenCacheEntry struct {
	claims  *Claims
	expires time.Time
}

type APITokenValidator struct {
	backendURL string
	client     *http.Client
	cache      map[string]tokenCacheEntry
	mu         sync.RWMutex
	cacheTTL   time.Duration
}

func NewAPITokenValidator(backendURL string) *APITokenValidator {
	return &APITokenValidator{
		backendURL: backendURL,
		client:     &http.Client{Timeout: 5 * time.Second},
		cache:      make(map[string]tokenCacheEntry),
		cacheTTL:   5 * time.Minute,
	}
}

func (v *APITokenValidator) IsAPIToken(token string) bool {
	return strings.HasPrefix(token, "tk_")
}

func (v *APITokenValidator) Validate(ctx context.Context, token string) (*Claims, error) {
	v.mu.RLock()
	if entry, ok := v.cache[token]; ok && time.Now().Before(entry.expires) {
		v.mu.RUnlock()
		return entry.claims, nil
	}
	v.mu.RUnlock()

	claims, err := v.verifyWithBackend(ctx, token)
	if err != nil {
		return nil, err
	}

	v.mu.Lock()
	v.cache[token] = tokenCacheEntry{
		claims:  claims,
		expires: time.Now().Add(v.cacheTTL),
	}
	v.mu.Unlock()

	return claims, nil
}

func (v *APITokenValidator) verifyWithBackend(ctx context.Context, token string) (*Claims, error) {
	url := fmt.Sprintf("%s/api/v1/tokens/verify", v.backendURL)
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)

	resp, err := v.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: backend token verify: %v", ErrAuthUnavailable, err)
	}
	defer resp.Body.Close()

	// The backend answers 401 for a token it rejects; any other failure says
	// nothing about the token.
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("token rejected: status %d", resp.StatusCode)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%w: backend token verify returned %d", ErrAuthUnavailable, resp.StatusCode)
	}

	var result struct {
		Valid    bool   `json:"valid"`
		UserID  string `json:"user_id"`
		Username string `json:"username"`
		Scopes  []string `json:"scopes"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("%w: decode backend token verify: %v", ErrAuthUnavailable, err)
	}

	if !result.Valid {
		return nil, fmt.Errorf("token not valid")
	}

	slog.Debug("API token validated", "username", result.Username)

	return &Claims{
		Subject:  result.UserID,
		Username: result.Username,
		Roles:    result.Scopes,
	}, nil
}
