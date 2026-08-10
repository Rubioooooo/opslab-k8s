#!/usr/bin/env bash

set -Eeuo pipefail

TARGET_URL="${TARGET_URL:-http://192.168.8.11/healthz}"
HOST_HEADER="${HOST_HEADER:-api.opslab.local}"
CONCURRENCY="${CONCURRENCY:-20}"
DURATION="${DURATION:-180}"

PIDS=()

usage() {
  cat <<USAGE
Usage:
  ./scripts/hpa-load-test.sh

Optional environment variables:
  TARGET_URL   Target URL
               default: http://192.168.8.11/healthz

  HOST_HEADER  HTTP Host header
               default: api.opslab.local

  CONCURRENCY  Number of concurrent request workers
               default: 20

  DURATION     Test duration in seconds
               default: 180

Examples:
  ./scripts/hpa-load-test.sh

  CONCURRENCY=10 DURATION=60 \
    ./scripts/hpa-load-test.sh

  CONCURRENCY=20 DURATION=180 \
    ./scripts/hpa-load-test.sh
USAGE
}

cleanup() {
  if ((${#PIDS[@]} > 0)); then
    for pid in "${PIDS[@]}"; do
      kill "${pid}" 2>/dev/null || true
    done

    wait "${PIDS[@]}" 2>/dev/null || true
  fi
}

trap cleanup EXIT
trap 'exit 130' INT TERM

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required." >&2
  exit 1
fi

if ! [[ "${CONCURRENCY}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: CONCURRENCY must be a positive integer." >&2
  exit 1
fi

if ! [[ "${DURATION}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: DURATION must be a positive integer." >&2
  exit 1
fi

echo "=== OpsLab HPA Load Test ==="
echo "Target URL : ${TARGET_URL}"
echo "Host       : ${HOST_HEADER}"
echo "Concurrency: ${CONCURRENCY}"
echo "Duration   : ${DURATION}s"
echo

echo "Checking target endpoint..."

if ! curl \
  -fsS \
  --max-time 3 \
  -o /dev/null \
  -H "Host: ${HOST_HEADER}" \
  "${TARGET_URL}"
then
  echo "ERROR: target endpoint is not reachable." >&2
  exit 1
fi

echo "Target check: PASS"
echo
echo "Starting HTTP load..."
echo "Press Ctrl+C to stop early."
echo

END_TIME=$((SECONDS + DURATION))

worker() {
  while ((SECONDS < END_TIME)); do
    curl \
      -fsS \
      --max-time 2 \
      -o /dev/null \
      -H "Host: ${HOST_HEADER}" \
      "${TARGET_URL}" \
      || true
  done
}

for ((i = 1; i <= CONCURRENCY; i++)); do
  worker &
  PIDS+=("$!")
done

wait "${PIDS[@]}"

PIDS=()

echo
echo "Load test completed."
