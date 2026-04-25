package health

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

type JWKSChecker interface {
	IsLoaded() bool
}

type Checker struct {
	backendURL string
	client     *http.Client
	jwks       JWKSChecker
}

func NewChecker(backendURL string) *Checker {
	return &Checker{
		backendURL: backendURL,
		client: &http.Client{
			Timeout: 5 * time.Second,
		},
	}
}

func (c *Checker) SetJWKS(j JWKSChecker) {
	c.jwks = j
}

type HealthResponse struct {
	Status string            `json:"status"`
	Checks map[string]string `json:"checks,omitempty"`
}

func (c *Checker) Handler(w http.ResponseWriter, r *http.Request) {
	checks := map[string]string{}
	allOK := true

	resp, err := c.client.Get(c.backendURL + "/health")
	if err != nil || resp.StatusCode != http.StatusOK {
		checks["backend"] = "unhealthy"
		allOK = false
	} else {
		checks["backend"] = "ok"
	}
	if resp != nil {
		resp.Body.Close()
	}

	if c.jwks != nil {
		if c.jwks.IsLoaded() {
			checks["jwks"] = "ok"
		} else {
			checks["jwks"] = "not_loaded"
		}
	}

	status := "ok"
	httpStatus := http.StatusOK
	if !allOK {
		status = "degraded"
		httpStatus = http.StatusOK // still 200 so k8s doesn't kill us for backend hiccup
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(httpStatus)
	json.NewEncoder(w).Encode(HealthResponse{
		Status: status,
		Checks: checks,
	})
}

func (c *Checker) LivenessHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":"ok"}`)
}
