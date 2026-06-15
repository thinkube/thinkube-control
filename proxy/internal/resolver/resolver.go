package resolver

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"sync"
	"sync/atomic"
	"time"

	"github.com/thinkube/thinkube-control/proxy/internal/metrics"
)

// ErrModelNotFound is the definitive "this model is unknown" signal from the
// backend (HTTP 404). It is the ONLY resolve failure that should surface to the
// client as 404 — every other failure (timeout, 5xx, transient 400 state/health
// flap) is transient and must be treated as retryable, never as not-found.
var ErrModelNotFound = errors.New("model not found")

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
	backendURL  string
	aliases     map[string]string
	client      *http.Client
	cache       sync.Map // cacheKey -> *cacheEntry (fresh, short TTL)
	lastGood    sync.Map // cacheKey -> *cacheEntry (last successful, longer TTL)
	cacheTTL    time.Duration
	lastGoodTTL time.Duration
	retryDelay  time.Duration

	// snapshot is a locally-synced copy of the control plane's whole resolution
	// table (keyed by model id AND serving name), refreshed in the background by
	// StartSnapshotSync. Resolve serves tier-agnostic requests from it without
	// any per-request control-plane call; a miss falls back to the live path.
	snapshot     atomic.Pointer[map[string]*ResolveResult]
	syncInterval time.Duration
}

func New(backendURL string, aliases map[string]string) *Resolver {
	return &Resolver{
		backendURL: backendURL,
		aliases:    aliases,
		client: &http.Client{
			Timeout: 5 * time.Second,
		},
		cacheTTL: 10 * time.Second,
		// A model -> backend mapping is stable, so a recently-successful
		// resolution is safe to reuse for a short window when the backend is
		// momentarily unreachable/flapping. Bounded so we never route to a
		// long-gone backend.
		lastGoodTTL: 5 * time.Minute,
		retryDelay:  150 * time.Millisecond,
		// How often the background snapshot is refreshed from the control plane.
		// The resolution table changes on the order of minutes (model loads), so
		// a few seconds keeps it fresh while reducing control-plane traffic from
		// per-request to ~one poll per interval.
		syncInterval: 5 * time.Second,
	}
}

func (r *Resolver) BackendURL() string {
	return r.backendURL
}

// Resolve maps a model alias/id to a backend. On a transient backend failure it
// retries once and, failing that, serves the last-known-good resolution within
// lastGoodTTL — so a momentary backend stall/flap does not surface as a 404.
// Only a definitive not-found (ErrModelNotFound) is returned without fallback.
func (r *Resolver) Resolve(ctx context.Context, model string, tier string) (*ResolveResult, error) {
	if alias, ok := r.aliases[model]; ok {
		slog.Debug("model alias resolved", "from", model, "to", alias)
		model = alias
	}

	// Snapshot-first: serve tier-agnostic requests from the locally-synced
	// table with no control-plane call. Tier-specific requests skip the snapshot
	// (it holds each model's default-tier resolution) and take the live path,
	// which honours tier.
	if tier == "" {
		if snap := r.snapshot.Load(); snap != nil {
			if res, ok := (*snap)[model]; ok {
				metrics.ResolveSource.WithLabelValues("snapshot").Inc()
				return res, nil
			}
		}
	}

	cacheKey := model + ":" + tier

	// Fresh cache hit.
	if entry, ok := r.cache.Load(cacheKey); ok {
		ce := entry.(*cacheEntry)
		if time.Now().Before(ce.expiresAt) {
			return ce.result, nil
		}
		r.cache.Delete(cacheKey)
	}

	// Live fetch (snapshot miss or tier-specific), with one retry on a transient
	// failure.
	metrics.ResolveSource.WithLabelValues("live").Inc()
	result, err := r.fetch(ctx, model, tier)
	if err != nil && !errors.Is(err, ErrModelNotFound) {
		metrics.ResolveResilience.WithLabelValues("retry").Inc()
		slog.Warn("resolve transient failure, retrying", "model", model, "error", err)
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(r.retryDelay):
		}
		result, err = r.fetch(ctx, model, tier)
	}

	if err == nil {
		r.cache.Store(cacheKey, &cacheEntry{result: result, expiresAt: time.Now().Add(r.cacheTTL)})
		r.lastGood.Store(cacheKey, &cacheEntry{result: result, expiresAt: time.Now().Add(r.lastGoodTTL)})
		return result, nil
	}

	// Definitive not-found: the model is genuinely unknown — do NOT serve stale.
	if errors.Is(err, ErrModelNotFound) {
		return nil, err
	}

	// Transient failure: absorb it with the last-known-good resolution if we
	// have a recent one.
	if entry, ok := r.lastGood.Load(cacheKey); ok {
		ce := entry.(*cacheEntry)
		if time.Now().Before(ce.expiresAt) {
			metrics.ResolveResilience.WithLabelValues("stale_serve").Inc()
			slog.Warn("resolve transient failure, serving last-known-good",
				"model", model, "error", err)
			return ce.result, nil
		}
		r.lastGood.Delete(cacheKey)
	}

	return nil, err
}

// fetch performs one resolve call against the backend and classifies the
// outcome. A backend 404 becomes ErrModelNotFound (definitive); every other
// error path is transient (retryable / stale-eligible).
func (r *Resolver) fetch(ctx context.Context, model, tier string) (*ResolveResult, error) {
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
		return nil, fmt.Errorf("resolve request failed: %w", err) // transient
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("%w: %s", ErrModelNotFound, model) // definitive
	}

	var result ResolveResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode resolve response: %w", err) // transient
	}

	if resp.StatusCode != http.StatusOK {
		// Non-200, non-404: a state/health flap (e.g. "Model is loading", "No
		// healthy backend serving model"). Transient — retry / serve stale.
		if result.Error != "" {
			return nil, fmt.Errorf("resolve returned %d: %s", resp.StatusCode, result.Error)
		}
		return nil, fmt.Errorf("resolve returned %d", resp.StatusCode)
	}

	if result.Error != "" {
		return &result, fmt.Errorf("%s", result.Error) // transient
	}

	return &result, nil
}

// StartSnapshotSync launches a background goroutine that refreshes the local
// resolution snapshot from the control plane's bulk endpoint every syncInterval,
// so Resolve serves requests without a per-request control-plane call. It does
// an immediate load, then ticks until ctx is cancelled.
func (r *Resolver) StartSnapshotSync(ctx context.Context) {
	go func() {
		if err := r.loadSnapshot(ctx); err != nil {
			slog.Warn("initial resolve-all snapshot load failed", "error", err)
		}
		t := time.NewTicker(r.syncInterval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				if err := r.loadSnapshot(ctx); err != nil {
					slog.Warn("resolve-all snapshot refresh failed", "error", err)
				}
			}
		}
	}()
}

// loadSnapshot fetches the control plane's full resolution table once and
// atomically swaps it in, keyed by both model id and serving name.
func (r *Resolver) loadSnapshot(ctx context.Context) error {
	reqURL := r.backendURL + "/api/v1/llm/models/resolve-all"
	req, err := http.NewRequestWithContext(ctx, "GET", reqURL, nil)
	if err != nil {
		return fmt.Errorf("create resolve-all request: %w", err)
	}

	resp, err := r.client.Do(req)
	if err != nil {
		return fmt.Errorf("resolve-all request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("resolve-all returned %d", resp.StatusCode)
	}

	var items []ResolveResult
	if err := json.NewDecoder(resp.Body).Decode(&items); err != nil {
		return fmt.Errorf("decode resolve-all response: %w", err)
	}

	m := make(map[string]*ResolveResult, len(items)*2)
	for i := range items {
		res := &items[i]
		if res.ModelID != "" {
			m[res.ModelID] = res
		}
		if res.ServingName != "" {
			m[res.ServingName] = res
		}
	}
	r.snapshot.Store(&m)
	slog.Debug("resolve-all snapshot refreshed", "models", len(items))
	return nil
}
