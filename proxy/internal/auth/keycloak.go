package auth

import (
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/big"
	"net/http"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type JWKSProvider struct {
	url        string
	keys       map[string]*rsa.PublicKey
	mu         sync.RWMutex
	client     *http.Client
	lastFetch  time.Time
	refreshTTL time.Duration
}

func NewJWKSProvider(keycloakURL, realm string) *JWKSProvider {
	return &JWKSProvider{
		url:        fmt.Sprintf("%s/realms/%s/protocol/openid-connect/certs", keycloakURL, realm),
		keys:       make(map[string]*rsa.PublicKey),
		client:     &http.Client{Timeout: 10 * time.Second},
		refreshTTL: 1 * time.Hour,
	}
}

func (p *JWKSProvider) FetchKeys(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, "GET", p.url, nil)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	resp, err := p.client.Do(req)
	if err != nil {
		return fmt.Errorf("fetch JWKS: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("JWKS returned %d", resp.StatusCode)
	}

	var jwks struct {
		Keys []struct {
			Kid string `json:"kid"`
			Kty string `json:"kty"`
			Alg string `json:"alg"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&jwks); err != nil {
		return fmt.Errorf("decode JWKS: %w", err)
	}

	newKeys := make(map[string]*rsa.PublicKey)
	for _, k := range jwks.Keys {
		if k.Kty != "RSA" {
			continue
		}

		nBytes, err := base64.RawURLEncoding.DecodeString(k.N)
		if err != nil {
			slog.Warn("invalid JWKS key N", "kid", k.Kid, "error", err)
			continue
		}
		eBytes, err := base64.RawURLEncoding.DecodeString(k.E)
		if err != nil {
			slog.Warn("invalid JWKS key E", "kid", k.Kid, "error", err)
			continue
		}

		n := new(big.Int).SetBytes(nBytes)
		e := 0
		for _, b := range eBytes {
			e = e*256 + int(b)
		}

		newKeys[k.Kid] = &rsa.PublicKey{N: n, E: e}
	}

	p.mu.Lock()
	p.keys = newKeys
	p.lastFetch = time.Now()
	p.mu.Unlock()

	slog.Info("JWKS keys loaded", "count", len(newKeys))
	return nil
}

func (p *JWKSProvider) GetKey(kid string) (*rsa.PublicKey, error) {
	p.mu.RLock()
	key, ok := p.keys[kid]
	stale := time.Since(p.lastFetch) > p.refreshTTL
	p.mu.RUnlock()

	if ok && !stale {
		return key, nil
	}

	if stale {
		if err := p.FetchKeys(context.Background()); err != nil {
			slog.Warn("JWKS refresh failed", "error", err)
			if ok {
				return key, nil
			}
			return nil, fmt.Errorf("JWKS unavailable: %w", err)
		}
		p.mu.RLock()
		key, ok = p.keys[kid]
		p.mu.RUnlock()
	}

	if !ok {
		return nil, fmt.Errorf("unknown key ID: %s", kid)
	}
	return key, nil
}

func (p *JWKSProvider) IsLoaded() bool {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return len(p.keys) > 0
}

type Claims struct {
	Subject  string
	Username string
	Email    string
	Roles    []string
}

type JWTValidator struct {
	jwks     *JWKSProvider
	issuer   string
	clientID string
}

func NewJWTValidator(keycloakURL, realm, clientID string) *JWTValidator {
	return &JWTValidator{
		jwks:     NewJWKSProvider(keycloakURL, realm),
		issuer:   fmt.Sprintf("%s/realms/%s", keycloakURL, realm),
		clientID: clientID,
	}
}

func (v *JWTValidator) JWKS() *JWKSProvider {
	return v.jwks
}

func (v *JWTValidator) Validate(tokenString string) (*Claims, error) {
	parser := jwt.NewParser(
		jwt.WithValidMethods([]string{"RS256"}),
		jwt.WithIssuer(v.issuer),
	)

	token, err := parser.Parse(tokenString, func(t *jwt.Token) (interface{}, error) {
		kid, ok := t.Header["kid"].(string)
		if !ok {
			return nil, fmt.Errorf("missing kid header")
		}
		return v.jwks.GetKey(kid)
	})
	if err != nil {
		return nil, fmt.Errorf("invalid token: %w", err)
	}

	mapClaims, ok := token.Claims.(jwt.MapClaims)
	if !ok {
		return nil, fmt.Errorf("unexpected claims type")
	}

	claims := &Claims{}

	if sub, ok := mapClaims["sub"].(string); ok {
		claims.Subject = sub
	}
	if name, ok := mapClaims["preferred_username"].(string); ok {
		claims.Username = name
	}
	if email, ok := mapClaims["email"].(string); ok {
		claims.Email = email
	}

	if realmAccess, ok := mapClaims["realm_access"].(map[string]interface{}); ok {
		if roles, ok := realmAccess["roles"].([]interface{}); ok {
			for _, r := range roles {
				if s, ok := r.(string); ok {
					claims.Roles = append(claims.Roles, s)
				}
			}
		}
	}

	return claims, nil
}
