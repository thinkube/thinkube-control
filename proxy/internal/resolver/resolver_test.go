package resolver

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
)

func newTestResolver(url string) *Resolver {
	r := New(url, nil)
	r.retryDelay = 0 // no backoff in tests
	return r
}

func okBody(model string) string {
	return `{"backend_url":"http://b","api_path":"/v1","model_id":"` + model +
		`","serving_name":"` + model + `","model_state":"available","tier":"performance"}`
}

// A successful resolve is cached: repeated calls hit the backend once.
func TestResolveSuccessCaches(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.Write([]byte(okBody("m")))
	}))
	defer srv.Close()

	r := newTestResolver(srv.URL)
	for i := 0; i < 3; i++ {
		res, err := r.Resolve(context.Background(), "m", "")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if res.ModelID != "m" {
			t.Fatalf("model_id = %q, want m", res.ModelID)
		}
	}
	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("backend calls = %d, want 1 (cached)", got)
	}
}

// A backend 404 is the definitive not-found and must surface as ErrModelNotFound.
func TestResolveNotFoundIsDefinitive(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	r := newTestResolver(srv.URL)
	if _, err := r.Resolve(context.Background(), "ghost", ""); !errors.Is(err, ErrModelNotFound) {
		t.Fatalf("err = %v, want ErrModelNotFound", err)
	}
}

// A transient backend failure (5xx) after a prior success is absorbed by serving
// the last-known-good resolution — not surfaced as an error.
func TestResolveStaleServeOnTransient(t *testing.T) {
	var fail int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if atomic.LoadInt32(&fail) == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.Write([]byte(okBody("m")))
	}))
	defer srv.Close()

	r := newTestResolver(srv.URL)
	if _, err := r.Resolve(context.Background(), "m", ""); err != nil {
		t.Fatalf("prime failed: %v", err)
	}
	r.cache.Delete("m:") // expire the fresh cache so the next call re-fetches
	atomic.StoreInt32(&fail, 1)

	res, err := r.Resolve(context.Background(), "m", "")
	if err != nil {
		t.Fatalf("expected last-known-good serve, got error: %v", err)
	}
	if res.ModelID != "m" {
		t.Fatalf("model_id = %q, want m", res.ModelID)
	}
}

// A transient failure with no last-known-good returns an error, but NOT
// ErrModelNotFound — so the handler maps it to a retryable 503, not a 404.
func TestResolveTransientWithoutCacheIsNotNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	r := newTestResolver(srv.URL)
	_, err := r.Resolve(context.Background(), "m", "")
	if err == nil {
		t.Fatal("expected an error")
	}
	if errors.Is(err, ErrModelNotFound) {
		t.Fatal("transient failure must not be ErrModelNotFound")
	}
}

// A transient failure on the first attempt is retried once; a success on the
// retry resolves cleanly.
func TestResolveRetryThenSucceed(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if atomic.AddInt32(&calls, 1) == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.Write([]byte(okBody("m")))
	}))
	defer srv.Close()

	r := newTestResolver(srv.URL)
	res, err := r.Resolve(context.Background(), "m", "")
	if err != nil {
		t.Fatalf("expected success after retry, got: %v", err)
	}
	if res.ModelID != "m" {
		t.Fatalf("model_id = %q, want m", res.ModelID)
	}
	if got := atomic.LoadInt32(&calls); got != 2 {
		t.Fatalf("backend calls = %d, want 2 (one retry)", got)
	}
}

// resolveAllBody returns a one-model resolve-all payload.
func resolveAllBody(modelID, servingName string) string {
	return `[{"backend_url":"http://b","api_path":"/v1","model_id":"` + modelID +
		`","serving_name":"` + servingName + `","model_state":"available","tier":"performance"}]`
}

// With a populated snapshot, concurrent requests for a snapshotted model resolve
// in-memory and make ZERO per-request control-plane (/resolve) calls.
func TestSnapshotHitAvoidsLiveCall(t *testing.T) {
	var liveCalls int32
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/llm/models/resolve-all", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte(resolveAllBody("m", "m-serve")))
	})
	mux.HandleFunc("/api/v1/llm/models/resolve", func(w http.ResponseWriter, _ *http.Request) {
		atomic.AddInt32(&liveCalls, 1)
		w.Write([]byte(okBody("m")))
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	r := newTestResolver(srv.URL)
	if err := r.loadSnapshot(context.Background()); err != nil {
		t.Fatalf("snapshot load: %v", err)
	}

	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			key := "m"
			if i%2 == 0 {
				key = "m-serve" // resolvable by serving name too
			}
			res, err := r.Resolve(context.Background(), key, "")
			if err != nil || res == nil || res.ModelID != "m" {
				t.Errorf("resolve(%q): res=%v err=%v", key, res, err)
			}
		}(i)
	}
	wg.Wait()

	if got := atomic.LoadInt32(&liveCalls); got != 0 {
		t.Fatalf("live /resolve calls = %d, want 0 (served from snapshot)", got)
	}
}

// A model absent from the snapshot falls back to exactly one live /resolve call.
func TestSnapshotMissFallsBackToLive(t *testing.T) {
	var liveCalls int32
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/llm/models/resolve-all", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte(resolveAllBody("known", "known")))
	})
	mux.HandleFunc("/api/v1/llm/models/resolve", func(w http.ResponseWriter, req *http.Request) {
		atomic.AddInt32(&liveCalls, 1)
		w.Write([]byte(okBody(req.URL.Query().Get("model"))))
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	r := newTestResolver(srv.URL)
	if err := r.loadSnapshot(context.Background()); err != nil {
		t.Fatalf("snapshot load: %v", err)
	}

	res, err := r.Resolve(context.Background(), "fresh-model", "")
	if err != nil {
		t.Fatalf("expected live fallback success, got: %v", err)
	}
	if res.ModelID != "fresh-model" {
		t.Fatalf("model_id = %q, want fresh-model", res.ModelID)
	}
	if got := atomic.LoadInt32(&liveCalls); got != 1 {
		t.Fatalf("live /resolve calls = %d, want 1 (snapshot miss -> one live resolve)", got)
	}
}
