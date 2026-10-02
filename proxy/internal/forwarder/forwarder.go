/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package forwarder

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Forwarder struct {
	client       *http.Client
	streamClient *http.Client
}

func New(timeout time.Duration) *Forwarder {
	noRedirect := func(req *http.Request, via []*http.Request) error {
		return http.ErrUseLastResponse
	}
	return &Forwarder{
		client: &http.Client{
			Timeout:       timeout,
			CheckRedirect: noRedirect,
		},
		streamClient: &http.Client{
			CheckRedirect: noRedirect,
		},
	}
}

func (f *Forwarder) Forward(ctx context.Context, backendURL string, body io.Reader, headers http.Header) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", backendURL, body)
	if err != nil {
		return nil, fmt.Errorf("create forward request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	if accept := headers.Get("Accept"); accept != "" {
		req.Header.Set("Accept", accept)
	}

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("forward request: %w", err)
	}

	return resp, nil
}

func (f *Forwarder) ForwardStreamRaw(ctx context.Context, backendURL string, body io.Reader) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", backendURL, body)
	if err != nil {
		return nil, fmt.Errorf("create stream request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "text/event-stream")

	resp, err := f.streamClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("stream request: %w", err)
	}
	return resp, nil
}

func (f *Forwarder) ForwardGet(ctx context.Context, backendURL string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", backendURL, nil)
	if err != nil {
		return nil, fmt.Errorf("create get request: %w", err)
	}
	req.Header.Set("Accept", "application/json")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("get request: %w", err)
	}

	return resp, nil
}
