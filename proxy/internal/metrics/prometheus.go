package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	RequestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "llm_gateway_requests_total",
		Help: "Total requests processed",
	}, []string{"protocol", "model", "backend", "status_code"})

	RequestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "llm_gateway_request_duration_seconds",
		Help:    "Request duration including backend latency",
		Buckets: []float64{0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0},
	}, []string{"protocol", "model", "backend"})

	TokensTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "llm_gateway_tokens_total",
		Help: "Token count by direction",
	}, []string{"protocol", "model", "backend", "direction"})

	ErrorsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "llm_gateway_errors_total",
		Help: "Errors by type",
	}, []string{"protocol", "error_type"})

	BackendHealth = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "llm_gateway_backend_health",
		Help: "Backend health status (1=healthy, 0=unhealthy)",
	}, []string{"backend", "model"})

	ModelRegistrySize = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "llm_gateway_model_registry_size",
		Help: "Number of models in the registry",
	})

	ActiveStreams = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "llm_gateway_active_streams",
		Help: "Currently active streaming connections",
	}, []string{"protocol", "model"})

	// ResolveResilience counts how often model resolution had to fall back on
	// its resilience paths: "retry" (a transient backend failure was retried)
	// and "stale_serve" (a transient failure was absorbed by serving the
	// last-known-good resolution). A rising rate means the backend is flapping.
	ResolveResilience = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "llm_gateway_resolve_resilience_total",
		Help: "Model-resolution resilience fallbacks (retry / stale_serve)",
	}, []string{"event"})
)
