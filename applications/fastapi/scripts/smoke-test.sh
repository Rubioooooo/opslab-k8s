#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  pwd
)"

cd "$ROOT_DIR"

PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
BASE_URL="http://${HOST}:${PORT}"
LOG_FILE="${LOG_FILE:-/tmp/opslab-api-local.log}"

if [[ ! -x "$PYTHON" ]]; then
  echo "FAIL: virtual-environment Python not found: $PYTHON" >&2
  exit 1
fi

if ss -lntH | awk '{print $4}' | grep -Eq ":${PORT}$"; then
  echo "FAIL: TCP port ${PORT} is already in use" >&2
  exit 1
fi

APP_NAME="opslab-api" \
APP_VERSION="v0.1.0" \
APP_ENV="development" \
"$PYTHON" -m uvicorn app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  >"$LOG_FILE" 2>&1 &

SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID"
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT

READY=false

for _ in $(seq 1 30); do
  if curl -fsS "${BASE_URL}/healthz" >/dev/null; then
    READY=true
    break
  fi

  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "FAIL: Uvicorn exited before becoming ready" >&2
    cat "$LOG_FILE" >&2
    exit 1
  fi

  sleep 1
done

if [[ "$READY" != "true" ]]; then
  echo "FAIL: API did not become ready" >&2
  cat "$LOG_FILE" >&2
  exit 1
fi

echo "# OpsLab API local smoke-test baseline"
echo
echo "recorded_at=$(date --iso-8601=seconds)"
echo "python=$("$PYTHON" --version 2>&1)"
echo "fastapi=$("$PYTHON" -c 'import fastapi; print(fastapi.__version__)')"
echo "uvicorn=$("$PYTHON" -c 'import uvicorn; print(uvicorn.__version__)')"
echo "base_url=$BASE_URL"
echo

echo "## GET /"
curl -fsS "${BASE_URL}/" | python3 -m json.tool
echo

echo "## GET /healthz"
curl -fsS "${BASE_URL}/healthz" | python3 -m json.tool
echo

echo "## GET /readyz"
curl -fsS "${BASE_URL}/readyz" | python3 -m json.tool
echo

DOCS_STATUS="$(
  curl -sS \
    -o /dev/null \
    -w '%{http_code}' \
    "${BASE_URL}/docs"
)"

OPENAPI_STATUS="$(
  curl -sS \
    -o /dev/null \
    -w '%{http_code}' \
    "${BASE_URL}/openapi.json"
)"

echo "docs_status=$DOCS_STATUS"
echo "openapi_status=$OPENAPI_STATUS"

if [[ "$DOCS_STATUS" != "200" ]]; then
  echo "FAIL: /docs did not return HTTP 200" >&2
  exit 1
fi

if [[ "$OPENAPI_STATUS" != "200" ]]; then
  echo "FAIL: /openapi.json did not return HTTP 200" >&2
  exit 1
fi

echo
echo "PASS: all local smoke tests succeeded"
