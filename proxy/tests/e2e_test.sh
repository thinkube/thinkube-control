#!/usr/bin/env bash
#
# LLM Gateway E2E test suite
# Run against a deployed gateway: ./e2e_test.sh [BASE_URL] [API_KEY]
#
set -euo pipefail

BASE_URL="${1:-https://llm.cmxela.com}"
API_KEY="${2:-}"
PASS=0
FAIL=0
SKIP=0

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

pass() { PASS=$((PASS+1)); green "  PASS: $1"; }
fail() { FAIL=$((FAIL+1)); red   "  FAIL: $1 — $2"; }
skip() { SKIP=$((SKIP+1)); yellow "  SKIP: $1 — $2"; }

auth_header() {
  if [ -n "$API_KEY" ]; then
    echo "x-api-key: $API_KEY"
  else
    echo "x-api-key: none"
  fi
}

section() { printf "\n=== %s ===\n" "$1"; }

# ─── Health & Metrics ───────────────────────────────────────

section "Health & Infrastructure"

# E0.1: Liveness
status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/livez")
[ "$status" = "200" ] && pass "liveness /livez" || fail "liveness /livez" "got $status"

# E0.2: Readiness
body=$(curl -s "$BASE_URL/health")
echo "$body" | grep -q '"status"' && pass "health /health" || fail "health /health" "unexpected body"

# E0.3: Metrics
body=$(curl -s "$BASE_URL/metrics")
echo "$body" | grep -q "llm_gateway_requests_total" && pass "metrics endpoint" || fail "metrics endpoint" "missing metric"

# ─── Auth ───────────────────────────────────────────────────

section "Authentication"

if [ -z "$API_KEY" ]; then
  skip "auth tests" "no API_KEY provided"
else
  # E1.1: No auth → 401
  status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/v1/models")
  [ "$status" = "401" ] && pass "no auth → 401" || fail "no auth → 401" "got $status"

  # E1.2: Valid auth → 200
  status=$(curl -s -o /dev/null -w "%{http_code}" -H "$(auth_header)" "$BASE_URL/v1/models")
  [ "$status" = "200" ] && pass "valid auth → 200" || fail "valid auth → 200" "got $status"
fi

# ─── OpenAI Passthrough ────────────────────────────────────

section "OpenAI Passthrough"

if [ -z "$API_KEY" ]; then
  skip "OpenAI tests" "no API_KEY provided"
else
  # E2.1: List models
  body=$(curl -s -H "$(auth_header)" "$BASE_URL/v1/models")
  echo "$body" | grep -q '"object":"list"' && pass "GET /v1/models" || fail "GET /v1/models" "bad response shape"

  # E2.2: Chat completion (non-streaming) — requires a model to be available
  model=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'] if d.get('data') else '')" 2>/dev/null || true)
  if [ -n "$model" ]; then
    resp=$(curl -s -H "$(auth_header)" -H "Content-Type: application/json" \
      "$BASE_URL/v1/chat/completions" \
      -d "{\"model\":\"$model\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello\"}]}")
    echo "$resp" | grep -q '"choices"' && pass "OpenAI chat completion" || fail "OpenAI chat completion" "bad response"

    # E2.3: Chat completion (streaming)
    resp=$(curl -s -N -H "$(auth_header)" -H "Content-Type: application/json" \
      "$BASE_URL/v1/chat/completions" \
      -d "{\"model\":\"$model\",\"max_tokens\":32,\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Say hi\"}]}" \
      --max-time 30 2>/dev/null | head -5)
    echo "$resp" | grep -q "data:" && pass "OpenAI streaming" || fail "OpenAI streaming" "no SSE data"
  else
    skip "OpenAI chat" "no models available"
  fi
fi

# ─── Anthropic Translation ─────────────────────────────────

section "Anthropic Translation"

if [ -z "$API_KEY" ]; then
  skip "Anthropic tests" "no API_KEY provided"
else
  # Need a model
  models_body=$(curl -s -H "$(auth_header)" "$BASE_URL/v1/models")
  model=$(echo "$models_body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'] if d.get('data') else '')" 2>/dev/null || true)

  if [ -n "$model" ]; then
    # E3.1: Anthropic non-streaming
    resp=$(curl -s -H "$(auth_header)" -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      "$BASE_URL/v1/messages" \
      -d "{\"model\":\"$model\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello\"}]}")
    echo "$resp" | grep -q '"type":"message"' && pass "Anthropic non-streaming" || fail "Anthropic non-streaming" "$(echo "$resp" | head -c 200)"

    # E3.2: Anthropic streaming
    resp=$(curl -s -N -H "$(auth_header)" -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      "$BASE_URL/v1/messages" \
      -d "{\"model\":\"$model\",\"max_tokens\":32,\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Say hi\"}]}" \
      --max-time 30 2>/dev/null | head -10)
    echo "$resp" | grep -q "event: message_start" && pass "Anthropic streaming" || fail "Anthropic streaming" "no message_start event"

    # E3.3: Anthropic with system prompt
    resp=$(curl -s -H "$(auth_header)" -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      "$BASE_URL/v1/messages" \
      -d "{\"model\":\"$model\",\"max_tokens\":32,\"system\":\"You are a pirate.\",\"messages\":[{\"role\":\"user\",\"content\":\"Greet me\"}]}")
    echo "$resp" | grep -q '"type":"message"' && pass "Anthropic with system prompt" || fail "Anthropic with system prompt" "bad response"

    # E3.4: Anthropic with tools
    resp=$(curl -s -H "$(auth_header)" -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      "$BASE_URL/v1/messages" \
      -d '{
        "model":"'"$model"'",
        "max_tokens":128,
        "tools":[{"name":"get_weather","description":"Get weather for a city","input_schema":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}],
        "messages":[{"role":"user","content":"What is the weather in NYC?"}]
      }')
    # Should get either a tool_use or text response — both are valid
    (echo "$resp" | grep -q '"type":"message"') && pass "Anthropic with tools" || fail "Anthropic with tools" "bad response"

    # E3.5: Stripped features don't error
    resp=$(curl -s -o /dev/null -w "%{http_code}" -H "$(auth_header)" -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      "$BASE_URL/v1/messages" \
      -d "{\"model\":\"$model\",\"max_tokens\":32,\"thinking\":{\"type\":\"enabled\",\"budget_tokens\":1024},\"metadata\":{\"user_id\":\"test\"},\"messages\":[{\"role\":\"user\",\"content\":\"Hi\"}]}")
    [ "$resp" = "200" ] && pass "stripped features (thinking, metadata) don't error" || fail "stripped features" "got $resp"

    # E3.6: Thinking blocks in passback are stripped
    resp=$(curl -s -o /dev/null -w "%{http_code}" -H "$(auth_header)" -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      "$BASE_URL/v1/messages" \
      -d '{
        "model":"'"$model"'",
        "max_tokens":32,
        "messages":[
          {"role":"user","content":"Hi"},
          {"role":"assistant","content":[{"type":"thinking","thinking":"internal thought"},{"type":"text","text":"Hello!"}]},
          {"role":"user","content":"How are you?"}
        ]
      }')
    [ "$resp" = "200" ] && pass "thinking blocks in passback stripped" || fail "thinking blocks passback" "got $resp"

  else
    skip "Anthropic chat" "no models available"
  fi
fi

# ─── Error Cases ────────────────────────────────────────────

section "Error Cases"

if [ -z "$API_KEY" ]; then
  skip "error case tests" "no API_KEY provided"
else
  # E4.1: Missing model field
  status=$(curl -s -o /dev/null -w "%{http_code}" -H "$(auth_header)" -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    "$BASE_URL/v1/messages" \
    -d '{"max_tokens":32,"messages":[{"role":"user","content":"Hi"}]}')
  [ "$status" = "400" ] && pass "missing model → 400" || fail "missing model" "got $status"

  # E4.2: Invalid JSON
  status=$(curl -s -o /dev/null -w "%{http_code}" -H "$(auth_header)" -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    "$BASE_URL/v1/messages" \
    -d 'not json')
  [ "$status" = "400" ] && pass "invalid JSON → 400" || fail "invalid JSON" "got $status"

  # E4.3: Unknown model
  status=$(curl -s -o /dev/null -w "%{http_code}" -H "$(auth_header)" -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    "$BASE_URL/v1/messages" \
    -d '{"model":"nonexistent-model-xyz","max_tokens":32,"messages":[{"role":"user","content":"Hi"}]}')
  [ "$status" = "404" ] && pass "unknown model → 404" || fail "unknown model" "got $status"

  # E4.4: Unregistered endpoint
  status=$(curl -s -o /dev/null -w "%{http_code}" -H "$(auth_header)" "$BASE_URL/v1/messages/batches")
  # Should be 404 or 405
  [ "$status" = "404" ] || [ "$status" = "405" ] && pass "batch endpoint → 404/405" || fail "batch endpoint" "got $status"
fi

# ─── Summary ───────────────────────────────────────────────

section "Results"
echo ""
green "Passed: $PASS"
[ "$FAIL" -gt 0 ] && red "Failed: $FAIL" || echo "Failed: 0"
[ "$SKIP" -gt 0 ] && yellow "Skipped: $SKIP" || echo "Skipped: 0"
echo ""

exit "$FAIL"
