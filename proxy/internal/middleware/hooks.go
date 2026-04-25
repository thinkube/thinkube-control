package middleware

import (
	"context"
	"net/http"
	"time"
)

type RequestContext struct {
	Protocol string
	UserID   string
	Roles    []string
	Model    *Model
	Backend  *Backend
	Metadata map[string]string
}

type Model struct {
	ID         string
	Name       string
	ServerType string
}

type Backend struct {
	ID   string
	Name string
	URL  string
	Type string
}

type TokenUsage struct {
	InputTokens  int
	OutputTokens int
}

type PreAuthHook interface {
	Name() string
	ProcessPreAuth(ctx context.Context, req *http.Request, rctx *RequestContext) error
}

type PreRequestHook interface {
	Name() string
	ProcessPreRequest(ctx context.Context, body []byte, rctx *RequestContext) ([]byte, error)
}

type PreForwardHook interface {
	Name() string
	ProcessPreForward(ctx context.Context, body []byte, rctx *RequestContext) ([]byte, error)
}

type PostResponseHook interface {
	Name() string
	ProcessPostResponse(ctx context.Context, rctx *RequestContext, statusCode int, duration time.Duration, usage *TokenUsage) error
}
