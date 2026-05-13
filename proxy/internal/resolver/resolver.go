package resolver

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"sync"
	"time"
)

type ResolveResult struct {
	BackendURL  string `json:"backend_url"`
	APIPath     string `json:"api_path"`
	ModelID     string `json:"model_id"`
	ServingName string `json:"serving_name"`
	ModelState  string `json:"model_state"`
	Tier        string `json:"tier"`
	Error       string `json:"error,omitempty"`
}

type cacheEntry struct {
	result    *ResolveResult
	expiresAt time.Time
}

type Resolver struct {
	backendURL string
	aliases    map[string]string
	client     *http.Client
	cache      sync.Map
	cacheTTL   time.Duration
}

func New(backendURL string, aliases map[string]string) *Resolver {
	return &Resolver{
		backendURL: backendURL,
		aliases:    aliases,
		client: &http.Client{
			Timeout: 5 * time.Second,
		},
		cacheTTL: 10 * time.Second,
	}
}

func (r *Resolver) BackendURL() string {
	return r.backendURL
}

func (r *Resolver) Resolve(ctx context.Context, model string, tier string) (*ResolveResult, error) {
	if alias, ok := r.aliases[model]; ok {
		slog.Debug("model alias resolved", "from", model, "to", alias)
		model = alias
	}

	// Check cache first
	cacheKey := model + ":" + tier
	if entry, ok := r.cache.Load(cacheKey); ok {
		ce := entry.(*cacheEntry)
		if time.Now().Before(ce.expiresAt) {
			return ce.result, nil
		}
		r.cache.Delete(cacheKey)
	}

	params := url.Values{}
	params.Set("model", model)
	if tier != "" {
		params.Set("tier", tier)
	}

	reqURL := fmt.Sprintf("%s/api/v1/llm/models/resolve?%s", r.backendURL, params.Encode())
	req, err := http.NewRequestWithContext(ctx, "GET", reqURL, nil)
	if err != nil {
		return nil, fmt.Errorf("create resolve request: %w", err)
	}

	resp, err := r.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("resolve request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("model not found: %s", model)
	}

	var result ResolveResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode resolve response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		if result.Error != "" {
			return nil, fmt.Errorf("%s", result.Error)
		}
		return nil, fmt.Errorf("resolve returned %d", resp.StatusCode)
	}

	if result.Error != "" {
		return &result, fmt.Errorf("%s", result.Error)
	}

	// Cache successful result
	r.cache.Store(cacheKey, &cacheEntry{
		result:    &result,
		expiresAt: time.Now().Add(r.cacheTTL),
	})

	return &result, nil
}
