#!/usr/bin/env bash
set -euo pipefail

# MCSS Smoke Test Script
# Verifies health endpoints and basic functionality without destructive operations

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "Running MCSS smoke tests..."

# Load environment (without printing secrets)
set -a
source .env 2>/dev/null || true
set +a

API_URL="${MCSS_SMOKE_API_URL:-http://localhost:${MCSS_API_PORT:-8000}}"

# Test 1: Health endpoint
echo "Test 1: GET /healthz"
HEALTH_RESPONSE=$(curl -fsS "$API_URL/healthz")
if [[ "$HEALTH_RESPONSE" == '{"status":"ok"}' ]]; then
    echo "  PASS: Health endpoint returns correct response"
else
    echo "  FAIL: Health endpoint returned: $HEALTH_RESPONSE"
    exit 1
fi

# Test 2: Ready endpoint
echo "Test 2: GET /readyz"
READY_RESPONSE=$(curl -fsS "$API_URL/readyz")
if [[ "$READY_RESPONSE" == '{"status":"ok"}' ]]; then
    echo "  PASS: Ready endpoint returns correct response"
else
    echo "  FAIL: Ready endpoint returned: $READY_RESPONSE"
    exit 1
fi

# Test 3: OpenAPI schema available (non-production)
echo "Test 3: GET /openapi.json"
OPENAPI_RESPONSE=$(curl -fsS "$API_URL/openapi.json" || echo "UNAVAILABLE")
if [[ "$OPENAPI_RESPONSE" != "UNAVAILABLE" ]]; then
    echo "  PASS: OpenAPI schema available"
else
    echo "  INFO: OpenAPI schema not available (expected in production)"
fi

# Test 4: CORS headers on health endpoint
echo "Test 4: CORS headers"
CORS_HEADERS=$(curl -fsS -D - -H "Origin: http://localhost:5173" "$API_URL/healthz" -o /dev/null | grep -i "access-control-allow-origin" || true)
if [[ -n "$CORS_HEADERS" ]]; then
    echo "  PASS: CORS headers present"
else
    echo "  INFO: CORS headers not detected (may be configured for specific origins)"
fi

# Test 5: Request ID header
echo "Test 5: Request ID header"
REQUEST_ID_HEADER=$(curl -fsS -D - "$API_URL/healthz" -o /dev/null | grep -i "x-request-id" || true)
if [[ -n "$REQUEST_ID_HEADER" ]]; then
    echo "  PASS: Request ID header present"
else
    echo "  FAIL: Request ID header missing"
    exit 1
fi

# Test 6: Security headers
echo "Test 6: Security headers"
SECURITY_HEADERS=$(curl -fsS -D - "$API_URL/healthz" -o /dev/null)
if echo "$SECURITY_HEADERS" | grep -qi "x-content-type-options: nosniff"; then
    echo "  PASS: X-Content-Type-Options header present"
else
    echo "  FAIL: X-Content-Type-Options header missing"
    exit 1
fi

if echo "$SECURITY_HEADERS" | grep -qi "x-frame-options: deny"; then
    echo "  PASS: X-Frame-Options header present"
else
    echo "  FAIL: X-Frame-Options header missing"
    exit 1
fi

if echo "$SECURITY_HEADERS" | grep -qi "referrer-policy:"; then
    echo "  PASS: Referrer-Policy header present"
else
    echo "  FAIL: Referrer-Policy header missing"
    exit 1
fi

# Test 7: Admin web accessible
echo "Test 7: Admin web accessible"
ADMIN_RESPONSE=$(curl -fsS -o /dev/null -w "%{http_code}" http://localhost:5173 || echo "000")
if [[ "$ADMIN_RESPONSE" == "200" ]]; then
    echo "  PASS: Admin web returns 200"
else
    echo "  FAIL: Admin web returned $ADMIN_RESPONSE"
    exit 1
fi

# Test 8: Widget demo accessible
echo "Test 8: Widget demo accessible"
WIDGET_RESPONSE=$(curl -fsS -o /dev/null -w "%{http_code}" http://localhost:5174 || echo "000")
if [[ "$WIDGET_RESPONSE" == "200" ]]; then
    echo "  PASS: Widget demo returns 200"
else
    echo "  FAIL: Widget demo returned $WIDGET_RESPONSE"
    exit 1
fi

# Test 9: Redis is healthy (when running under expanded profile)
echo "Test 9: Redis connectivity"
if docker compose ps --services --filter "status=running" 2>/dev/null | grep -qx "redis"; then
    if docker compose exec -T redis redis-cli ping | grep -qx "PONG"; then
        echo "  PASS: Redis responds to ping"
    else
        echo "  FAIL: Redis is not responding"
        exit 1
    fi
else
    echo "  INFO: Redis deferred in v1.1 MVP profile (optional via --profile expanded)"
fi

# Test 10: WuKongIM is healthy (when running under expanded profile)
echo "Test 10: WuKongIM health"
if docker compose ps --services --filter "status=running" 2>/dev/null | grep -qx "wukongim"; then
    if docker compose exec -T wukongim wget -q -Y off -O /dev/null http://localhost:5001/health; then
        echo "  PASS: WuKongIM health endpoint responds"
    else
        echo "  FAIL: WuKongIM health endpoint is unavailable"
        exit 1
    fi
else
    echo "  INFO: WuKongIM deferred in v1.1 MVP profile (optional via --profile expanded)"
fi

echo ""
echo "All smoke tests passed!"
echo ""
echo "MCSS is ready for development."
