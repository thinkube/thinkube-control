package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"
)

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
		return nil, fmt.Errorf("backend token verify: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("token rejected: status %d", resp.StatusCode)
	}

	var result struct {
		Valid    bool   `json:"valid"`
		UserID  string `json:"user_id"`
		Username string `json:"username"`
		Scopes  []string `json:"scopes"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
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
