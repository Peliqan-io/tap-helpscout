#!/usr/bin/env bash
set -euo pipefail
TAP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PIP_CACHE="${HOME}/.cache/pip-tap-helpscout"
mkdir -p "$PIP_CACHE"

echo "==> Running regression tests in python:3.11-slim"
docker run --rm \
  -v "$TAP_DIR":/tap \
  -v "$PIP_CACHE":/root/.cache/pip \
  -w /tap \
  -e TAP_HELPSCOUT_CLIENT_ID="${TAP_HELPSCOUT_CLIENT_ID:?}" \
  -e TAP_HELPSCOUT_CLIENT_SECRET="${TAP_HELPSCOUT_CLIENT_SECRET:?}" \
  -e TAP_HELPSCOUT_REFRESH_TOKEN="${TAP_HELPSCOUT_REFRESH_TOKEN:?}" \
  -e TAP_HELPSCOUT_INCLUDE_STREAMS="${TAP_HELPSCOUT_INCLUDE_STREAMS:-mailboxes,users,teams}" \
  -e TAP_HELPSCOUT_START_DATE="${TAP_HELPSCOUT_START_DATE:-2024-01-01T00:00:00Z}" \
  -e AES_SECRET_KEY="peliqan-test-key" \
  python:3.11-slim \
  bash -c "
    apt-get update -qq && apt-get install -y -qq git > /dev/null
    python -m venv /venv
    /venv/bin/pip install -e . pytest -q
    cd tests/regression && /venv/bin/python -m pytest test_regression.py -v
  "
